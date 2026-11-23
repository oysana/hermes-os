import http.server
import importlib.util
from pathlib import Path
import socket
import tempfile
import threading
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'scripts/browser-access.py'
spec = importlib.util.spec_from_file_location('browser_access', SOURCE)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def fake_deps(**overrides):
    deps = {
        'read_cdp_url': lambda home: overrides.get('cdp_url', ''),
        'fetch': lambda url, *a, **k: {'listening': False, 'error': 'ConnectionRefusedError'},
        'listener': lambda port: [],
        'entry': lambda port: None,
        'pids': lambda udd: [],
        'launch': lambda entry: (_ for _ in ()).throw(AssertionError('launch must not run')),
        'sleep': lambda s: None,
        'monotonic': _FakeClock(),
    }
    deps.update(overrides)
    return deps


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 30.0
        return self.t


def make_home(root, profile, cdp_url='http://127.0.0.1:9222'):
    home = helper.profile_home(profile, root)
    (home / 'config.yaml').parent.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('browser:\n  cdp_url: %s\n' % cdp_url)
    return home


class MayLaunchTests(unittest.TestCase):
    """Truth table locked to verification/browser-access/laws/browser_access.bend."""

    def test_launch_only_down_ensured_loopback_with_entry(self):
        for listening in (False, True):
            for ensure in (False, True):
                for loopback in (False, True):
                    for entry in (False, True):
                        expected = (not listening) and ensure and loopback and entry
                        self.assertIs(
                            helper.may_launch(listening, ensure, loopback, entry),
                            expected,
                            (listening, ensure, loopback, entry))

    def test_active_endpoint_never_launches(self):
        self.assertFalse(helper.may_launch(True, True, True, True))


class UrlParsingTests(unittest.TestCase):
    def test_ws_and_wss_normalise_to_http(self):
        self.assertEqual(helper.http_base('ws://127.0.0.1:9222/'), 'http://127.0.0.1:9222')
        self.assertEqual(helper.http_base('wss://127.0.0.1:9222'), 'https://127.0.0.1:9222')

    def test_host_port_extraction(self):
        self.assertEqual(helper.endpoint_host_port('http://127.0.0.1:9222'), ('127.0.0.1', 9222))
        self.assertEqual(helper.endpoint_host_port('http://example.com'), ('example.com', 80))

    def test_loopback_detection(self):
        self.assertTrue(helper.is_loopback('127.0.0.1'))
        self.assertTrue(helper.is_loopback('localhost'))
        self.assertFalse(helper.is_loopback('10.0.0.5'))


class EndpointFixtureTests(unittest.TestCase):
    def test_fetch_version_against_mock_cdp_endpoint(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"Browser": "Chrome/999", "Protocol-Version": "1.3"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = helper.fetch_version(f'http://127.0.0.1:{server.server_port}', timeout=2)
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(result['listening'])
        self.assertEqual(result['browser'], 'Chrome/999')

    def test_fetch_version_closed_port_reports_down(self):
        result = helper.fetch_version(f'http://127.0.0.1:{free_port()}', timeout=2)
        self.assertFalse(result['listening'])
        self.assertIn('error', result)


class DesktopEntryTests(unittest.TestCase):
    def test_entry_found_by_debug_port(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            (d / 'google-chrome.desktop').write_text(
                '[Desktop Entry]\nName=Google Chrome\n'
                'Exec=/opt/chrome --user-data-dir=/home/u/.config/chrome-owner '
                '--remote-debugging-port=9222 %U\n')
            entry = helper.find_desktop_entry(9222, dirs=[d])
            self.assertIsNotNone(entry)
            self.assertEqual(entry['id'], 'google-chrome')
            self.assertEqual(entry['user_data_dir'], '/home/u/.config/chrome-owner')

    def test_wrong_port_or_no_flag_not_found(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            (d / 'plain.desktop').write_text(
                '[Desktop Entry]\nExec=/opt/chrome --remote-debugging-port=9333 %U\n')
            self.assertIsNone(helper.find_desktop_entry(9222, dirs=[d]))
            (d / 'plain.desktop').write_text('[Desktop Entry]\nExec=/opt/chrome %U\n')
            self.assertIsNone(helper.find_desktop_entry(9222, dirs=[d]))


class LaunchTests(unittest.TestCase):
    def test_first_successful_runner_wins(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd[0])
            class Result:
                returncode = 0 if cmd[0] == 'gio' else 1
            return Result()

        ok, via, tried = helper.launch_entry(
            {'id': 'google-chrome', 'path': '/x/google-chrome.desktop'}, runner=runner)
        self.assertTrue(ok)
        self.assertEqual(via, 'gio')
        self.assertEqual(calls, ['gtk-launch', 'gio'])

    def test_all_attempts_fail_reports_tried(self):
        def runner(cmd, **kw):
            raise FileNotFoundError(cmd[0])

        ok, via, tried = helper.launch_entry(
            {'id': 'x', 'path': '/x.desktop'}, runner=runner)
        self.assertFalse(ok)
        self.assertIsNone(via)
        self.assertEqual(len(tried), 3)


class RunTests(unittest.TestCase):
    def _home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        return root

    def test_listening_endpoint_is_ok_without_launch(self):
        root = self._home()
        make_home(root, 'yqecea')
        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['fetch'] = lambda url, *a, **k: {'listening': True, 'browser': 'Chrome/999'}
        record, rc = helper.run('yqecea', root, ensure=False, wait_s=0, deps=deps)
        self.assertEqual(rc, 0)
        self.assertTrue(record['ok'])
        self.assertEqual(record['ensure']['requested'], False)

    def test_down_without_ensure_reports_remedy(self):
        root = self._home()
        make_home(root, 'yqecea')
        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['entry'] = lambda port: {'id': 'google-chrome', 'path': '/x.desktop',
                                      'user_data_dir': '/u'}
        record, rc = helper.run('yqecea', root, ensure=False, wait_s=0, deps=deps)
        self.assertEqual(rc, 1)
        self.assertFalse(record['ok'])
        self.assertIn('--ensure', record['remedy'])

    def test_ensure_launches_entry_and_endpoint_comes_up(self):
        root = self._home()
        make_home(root, 'yqecea')
        responses = iter([{'listening': False, 'error': 'refused'},
                          {'listening': True, 'browser': 'Chrome/999'}])
        launched = []

        def launch(entry):
            launched.append(entry['id'])
            return True, 'gtk-launch', ['gtk-launch: rc=0']

        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['fetch'] = lambda url, *a, **k: next(responses)
        deps['entry'] = lambda port: {'id': 'google-chrome', 'path': '/x.desktop',
                                      'user_data_dir': '/u'}
        deps['launch'] = launch
        deps['monotonic'] = lambda: 0.0
        record, rc = helper.run('yqecea', root, ensure=True, wait_s=5, deps=deps)
        self.assertEqual(rc, 0)
        self.assertEqual(launched, ['google-chrome'])
        self.assertEqual(record['ensure']['via'], 'gtk-launch')

    def test_ensure_with_running_owner_browser_never_restarts(self):
        root = self._home()
        make_home(root, 'yqecea')
        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['entry'] = lambda port: {'id': 'google-chrome', 'path': '/x.desktop',
                                      'user_data_dir': '/u'}
        deps['launch'] = lambda entry: (True, 'gtk-launch', ['gtk-launch: rc=0'])
        deps['pids'] = lambda udd: [4242]
        record, rc = helper.run('yqecea', root, ensure=True, wait_s=0, deps=deps)
        self.assertEqual(rc, 1)
        self.assertIn('never restarts', record['remedy'])

    def test_ensure_without_entry_does_not_launch(self):
        root = self._home()
        make_home(root, 'yqecea')
        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        record, rc = helper.run('yqecea', root, ensure=True, wait_s=0, deps=deps)
        self.assertEqual(rc, 1)
        self.assertIn('desktop entry', record['remedy'])
        self.assertFalse(record['ensure']['launched'])

    def test_non_loopback_ensure_refused(self):
        root = self._home()
        make_home(root, 'yqecea', cdp_url='http://10.0.0.5:9222')
        deps = fake_deps(cdp_url='http://10.0.0.5:9222')
        deps['entry'] = lambda port: {'id': 'e', 'path': '/x.desktop', 'user_data_dir': '/u'}
        record, rc = helper.run('yqecea', root, ensure=True, wait_s=0, deps=deps)
        self.assertEqual(rc, 1)
        self.assertIn('not loopback', record['remedy'])
        self.assertFalse(record['ensure']['launched'])

    def test_missing_config_yaml_is_usage_error(self):
        root = self._home()
        record, rc = helper.run('ghost', root, ensure=False, wait_s=0, deps=fake_deps())
        self.assertEqual(rc, 2)
        self.assertIn('config.yaml', record['error'])

    def test_unconfigured_cdp_url_is_usage_error(self):
        root = self._home()
        make_home(root, 'default')
        deps = fake_deps(cdp_url='')
        record, rc = helper.run('default', root, ensure=False, wait_s=0, deps=deps)
        self.assertEqual(rc, 2)
        self.assertIn('cdp_url', record['error'])

    def test_profile_home_mapping(self):
        root = Path('/x/.hermes')
        self.assertEqual(helper.profile_home('default', root), root)
        self.assertEqual(helper.profile_home('', root), root)
        self.assertEqual(helper.profile_home('yqecea', root), root / 'profiles/yqecea')
        self.assertEqual(helper.profile_home('sqecea', root), root / 'profiles/sqecea')


if __name__ == '__main__':
    unittest.main()


def serve_json(routes):
    """Mock HTTP endpoint answering each path in routes with its JSON body; others 404."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class RelayTests(unittest.TestCase):
    """The owner-Chrome relay answers on the port even while Chrome is closed (16.11)."""

    def _probe(self, state):
        server = serve_json({
            '/healthz': '{"upstream_state": "%s", "profile_dir": "/h/.config/google-chrome"}' % state,
            '/json/version': '{"Browser": "Google Chrome (owner profile)", "Protocol-Version": "1.3"}'})
        try:
            return helper.fetch_version(f'http://127.0.0.1:{server.server_port}', timeout=2)
        finally:
            server.shutdown()
            server.server_close()

    def test_relay_without_browser_is_down(self):
        result = self._probe('waiting-browser')
        self.assertFalse(result['listening'])
        self.assertTrue(result['relay'])
        self.assertEqual(result['upstream_state'], 'waiting-browser')
        self.assertEqual(result['profile_dir'], '/h/.config/google-chrome')

    def test_relay_connected_is_up(self):
        result = self._probe('connected')
        self.assertTrue(result['listening'])
        self.assertEqual(result['browser'], 'Google Chrome (owner profile)')

    def test_entry_for_default_chrome_profile_dir_found_without_port(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            (d / 'google-chrome.desktop').write_text(
                '[Desktop Entry]\nName=Google Chrome\n'
                'Exec=/x/chrome-nvidia-safe --profile-directory=Default --ozone-platform=x11 %U\n')
            (d / 'other.desktop').write_text(
                '[Desktop Entry]\nExec=/opt/chrome --user-data-dir=/h/.config/chrome-agent %U\n')
            entry = helper.find_desktop_entry(9222, dirs=[d], profile_dir='/h/.config/google-chrome',
                                              default_chrome_dir='/h/.config/google-chrome')
            self.assertEqual(entry['id'], 'google-chrome')
            self.assertEqual(entry['user_data_dir'], '/h/.config/google-chrome')

    def test_entry_for_explicit_user_data_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            (d / 'agent.desktop').write_text(
                '[Desktop Entry]\nExec=/opt/chrome --user-data-dir=/h/.config/chrome-agent %U\n')
            entry = helper.find_desktop_entry(9222, dirs=[d], profile_dir='/h/.config/chrome-agent',
                                              default_chrome_dir='/h/.config/google-chrome')
            self.assertEqual(entry['id'], 'agent')
            self.assertIsNone(helper.find_desktop_entry(
                9222, dirs=[d], profile_dir='/h/.config/google-chrome',
                default_chrome_dir='/h/.config/google-chrome'))

    def test_default_dir_browser_pids_match_only_main_default_chrome(self):
        procs = [(10, '/opt/google/chrome/chrome --profile-directory=Default --ozone-platform=x11'),
                 (11, '/opt/google/chrome/chrome --type=renderer --lang=en'),
                 (12, '/opt/google/chrome/chrome --user-data-dir=/h/.config/chrome-agent --headless=new'),
                 (13, '/usr/bin/python3 relay.py')]
        self.assertEqual(helper.owner_browser_pids('/h/.config/google-chrome', procs=procs,
                                                   default_chrome_dir='/h/.config/google-chrome'), [10])
        self.assertEqual(helper.owner_browser_pids('/h/.config/chrome-agent', procs=procs,
                                                   default_chrome_dir='/h/.config/google-chrome'), [12])

    def test_relay_down_ensure_launches_entry_found_by_profile_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        make_home(root, 'yqecea')
        responses = iter([{'listening': False, 'relay': True, 'upstream_state': 'waiting-browser',
                           'profile_dir': '/h/.config/google-chrome'},
                          {'listening': True, 'relay': True, 'upstream_state': 'connected'}])
        asked = []

        def entry(port, profile_dir=None):
            asked.append((port, profile_dir))
            return {'id': 'google-chrome', 'path': '/x.desktop', 'user_data_dir': profile_dir}

        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['fetch'] = lambda url, *a, **k: next(responses)
        deps['entry'] = entry
        deps['launch'] = lambda e: (True, 'gtk-launch', ['gtk-launch: rc=0'])
        deps['monotonic'] = lambda: 0.0
        record, rc = helper.run('yqecea', root, ensure=True, wait_s=5, deps=deps)
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [(9222, '/h/.config/google-chrome')])

    def test_pending_approval_remedy_names_the_allow_click(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        make_home(root, 'yqecea')
        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['fetch'] = lambda url, *a, **k: {'listening': False, 'relay': True,
                                              'upstream_state': 'connecting',
                                              'profile_dir': '/h/.config/google-chrome'}
        deps['entry'] = lambda port, profile_dir=None: {'id': 'google-chrome', 'path': '/x.desktop',
                                                        'user_data_dir': profile_dir}
        deps['pids'] = lambda udd: [4242]
        record, rc = helper.run('yqecea', root, ensure=False, wait_s=0, deps=deps)
        self.assertEqual(rc, 1)
        self.assertIn('Allow', record['remedy'])

    def test_run_reaches_real_entry_lookup_through_relay_profile_dir(self):
        """The call form run() uses must fit the real find_desktop_entry (live bug 16.11)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        make_home(root, 'yqecea')
        apps = root / 'apps'
        apps.mkdir()
        (apps / 'google-chrome.desktop').write_text(
            '[Desktop Entry]\nExec=/x/chrome-nvidia-safe --profile-directory=Default %U\n')
        deps = fake_deps(cdp_url='http://127.0.0.1:9222')
        deps['fetch'] = lambda url, *a, **k: {'listening': False, 'relay': True,
                                              'upstream_state': 'waiting-browser',
                                              'profile_dir': '/h/.config/google-chrome'}
        deps['entry'] = lambda port, **kw: helper.find_desktop_entry(
            port, dirs=[apps], default_chrome_dir='/h/.config/google-chrome', **kw)
        record, rc = helper.run('yqecea', root, ensure=False, wait_s=0, deps=deps)
        self.assertEqual(rc, 1)
        self.assertEqual(record['desktop_entry']['id'], 'google-chrome')
