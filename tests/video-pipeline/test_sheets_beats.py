"""Public CLI coverage: each known visible beat appears exactly once, offline."""
import json
import hashlib
import importlib.util
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(os.environ.get('SAMPLER_SOURCE', Path.home() / '.hermes/skills/creative/visual-output/scripts/video/vo_video.py')).resolve()


class SheetsCoverage(unittest.TestCase):
    def fixture(self, root, single=False):
        work = root / 'work'
        work.mkdir()
        colors = [(255, 0, 0)] if single else [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        counts = [45] if single else [36, 54, 36]
        pixels = b''.join(bytes(color) * (64 * 64 * count) for color, count in zip(colors, counts))
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                        '-s', '64x64', '-r', '30', '-i', 'pipe:0', '-c:v', 'libx264rgb',
                        '-crf', '0', str(work / 'fixture.mp4')], input=pixels, check=True, timeout=10)
        # Metadata from the producer's event contract: cue starts at beatstart+LEAD(.15);
        # cue end includes .15 after speech. The differently sized holds stay in their beats.
        meta = {'duration': 1.5 if single else 4.2,
                'scenes': [{'start': 0}] if single else [{'start': 0}, {'start': 3.0}],
                'cues': [{'start': .15, 'end': .80}] if single else
                        [{'start': .15, 'end': .80}, {'start': 1.35, 'end': 2.20},
                         {'start': 3.15, 'end': 3.45}]}
        (work / 'fixture.json').write_text(json.dumps(meta))
        if not single:
            (work / 'join.txt').write_text("file 'fixture.mp4'\n")
        return work

    def actual_colors(self, root, work, beats):
        out = root / 'sheets'
        argv = [sys.executable, '-B', str(SOURCE), 'sheets', '--work', str(work), '--out', str(out)]
        if beats:
            argv.append('--beats')
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        colors = []
        for path in sorted(out.glob('s_*.png')):
            rgb = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
                                           '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                                           'pipe:1'], timeout=10)
            colors.append(('red', 'green', 'blue')[max(range(3), key=lambda i: rgb[i])])
        self.assertTrue(list(out.glob('sheet*.jpg')), 'Public contact-sheet output missing')
        return colors

    def test_every_beat_once_including_first_and_last(self):
        with tempfile.TemporaryDirectory(prefix='sampler-') as directory:
            root = Path(directory)
            self.assertEqual(self.actual_colors(root, self.fixture(root), True), ['red', 'green', 'blue'])

    def test_scene_mode_keeps_each_scene_final_state(self):
        with tempfile.TemporaryDirectory(prefix='sampler-') as directory:
            root = Path(directory)
            self.assertEqual(self.actual_colors(root, self.fixture(root), False), ['green', 'blue'])

    def test_single_beat_without_join_list(self):
        with tempfile.TemporaryDirectory(prefix='sampler-') as directory:
            root = Path(directory)
            self.assertEqual(self.actual_colors(root, self.fixture(root, single=True), True), ['red'])


    def question_fixture(self, root, final_frame_only=False):
        work = root / 'work'
        work.mkdir()
        # Valid producer timing: speech .5 + LEAD .15 + pause3 + TAIL .4 =4.05s,
        # roundedup to122 frames at30fps. Countdownends3.65s ->110 movingframes.
        # A longer animation clamps movingframes to121 and settles only on frame121.
        moving = 121 if final_frame_only else 110
        pixels = bytes((255, 0, 0)) * (64 * 64 * moving)
        pixels += bytes((0, 255, 0)) * (64 * 64 * (122 - moving))
        pixels += bytes((0, 0, 255)) * (64 * 64 * 45)
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                        '-s', '64x64', '-r', '30', '-i', 'pipe:0', '-c:v', 'libx264rgb',
                        '-crf', '0', str(work / 'fixture.mp4')], input=pixels, check=True, timeout=10)
        (work / 'fixture.json').write_text(json.dumps({'duration':167/30,
            'scenes':[{'start':0}], 'cues':[{'start':.15,'end':.8},
            {'start':122/30+.15,'end':122/30+.8}]}))
        return work

    def test_long_question_countdown_is_finished(self):
        with tempfile.TemporaryDirectory(prefix='sampler-') as directory:
            root = Path(directory)
            self.assertEqual(self.actual_colors(root, self.question_fixture(root), True), ['green', 'blue'])

    def test_last_frame_after_clamped_animation_is_selected(self):
        with tempfile.TemporaryDirectory(prefix='sampler-') as directory:
            root = Path(directory)
            self.assertEqual(self.actual_colors(root, self.question_fixture(root, final_frame_only=True), True),
                             ['green', 'blue'])

    def test_real_producer_writer_and_encoder_preserve_planned_last_frames(self):
        spec = importlib.util.spec_from_file_location('video_producer_under_test', SOURCE)
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        fixture = Path(__file__).resolve().parent / 'fixtures'
        with tempfile.TemporaryDirectory(prefix='producer-') as directory:
            root = Path(directory)
            for name in ['red.jpg', 'green.jpg', 'blue.jpg', 'audio.wav']:
                shutil.copy2(fixture / name, root / name)
            # Same loadbearing real movingframe durations as the frozen canonical RED.
            concat = [('red.jpg', 1/30)] * 21 + [('green.jpg', .8), ('blue.jpg', 1.2)]
            out = root / 'fixture.mp4'
            producer.encode_frames(str(root), concat, str(out), 81)
            frames = (root / 'frames.txt').read_text()
            self.assertEqual(frames.count('option framerate 30'), 24)
            self.assertTrue(frames.endswith("file 'blue.jpg'\noption framerate 30\n"))
            rgb = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(out), '-vf',
                r'select=eq(n\,20)+eq(n\,44)+eq(n\,80),scale=1:1', '-fps_mode', 'vfr',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], timeout=10)
            self.assertEqual(len(rgb), 9)
            colors = [('red', 'green', 'blue')[max(range(3), key=lambda j: rgb[i+j])]
                      for i in range(0,9,3)]
            self.assertEqual(colors, ['red', 'green', 'blue'])
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
                '-show_streams', '-of', 'json', str(out)], timeout=10))
            video = next(v for v in probe['streams'] if v['codec_type'] == 'video')
            self.assertEqual((video['nb_read_frames'], video['r_frame_rate']), ('81', '30/1'))
            self.assertAlmostEqual(float(video['duration']), 2.7, places=5)
            packets = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-f', 'concat',
                '-safe', '0', '-i', str(root / 'frames.txt'), '-show_entries',
                'packet=pts_time,duration_time', '-of', 'json'], timeout=10))['packets']
            print('PRODUCER_PROOF=' + json.dumps({'real_function': 'encode_frames',
                'candidate_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                'colors_at_indices20_44_80': colors, 'encoded_frames': video['nb_read_frames'],
                'encoded_rate': video['r_frame_rate'], 'encoded_duration': video['duration'],
                'concat_PTS': [v['pts_time'] for v in packets], 'per_JPEG_option_count':24,
                'frames_txt_sha256': hashlib.sha256((root / 'frames.txt').read_bytes()).hexdigest(),
                'MP4_sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
                'input_hashes': {n:hashlib.sha256((root / n).read_bytes()).hexdigest()
                    for n in ['red.jpg','green.jpg','blue.jpg','audio.wav']},
                'no_TTS': True, 'temp_cleanup': 'TemporaryDirectory after test'}))

if __name__ == '__main__':
    unittest.main()
