"""Summarize fully paginated native GWS output; failures are never an empty calendar."""
from collections import defaultdict
from datetime import datetime,timedelta
import json
import sys
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Almaty')


def render(pages, now):
    byday=defaultdict(list);seen_page=False;next_page=None
    for page in pages:
        if not isinstance(page,dict) or 'error' in page or not isinstance(page.get('items',[]),list):
            raise ValueError('Calendar API result is not a successful page')
        seen_page=True;next_page=page.get('nextPageToken')
        if next_page is not None and not isinstance(next_page,str):raise ValueError('Invalid page cursor')
        for event in page.get('items',[]):
            if not isinstance(event,dict) or not isinstance(event.get('start'),dict):raise ValueError('Invalid calendar event')
            start=event['start'];title=event.get('summary','?')
            if not isinstance(title,str):raise ValueError('Invalid event title')
            if start.get('dateTime'):
                moment=datetime.fromisoformat(start['dateTime'].replace('Z','+00:00'))
                if moment.tzinfo is None:raise ValueError('Calendar time lacks timezone')
                moment=moment.astimezone(ZONE);day=moment.date().isoformat();clock=moment.strftime('%H:%M')
            else:
                day=datetime.strptime(start['date'],'%Y-%m-%d').date().isoformat();clock='весь день'
            byday[day].append((clock,title))
    if not seen_page or next_page:raise ValueError('Calendar pagination incomplete')
    today=now.astimezone(ZONE).date();flags=[]
    for offset in range(8):
        day=today+timedelta(days=offset);events=byday.get(day.isoformat(),[])
        if day.weekday()<5 and not events:flags.append(f'ПУСТОЙ БУДЕНЬ: {day.isoformat()} — ни одного события')
        titles=defaultdict(list)
        for clock,title in events:titles[title.strip().lower()].append(clock)
        for title,times in titles.items():
            if len(times)>1:flags.append(f'ДУБЛЬ?: {day.isoformat()} «{title}» в {times}')
    return flags or ['(тихо: календарь чист)']


def main():
    try:
        pages=(json.loads(line) for line in sys.stdin if line.strip())
        lines=render(pages,datetime.now(ZONE))
    except (ValueError,KeyError,TypeError):
        print('Проверка календаря не завершена: API или неполная выдача. Не считать календарь пустым.',file=sys.stderr)
        return 1
    print('\n'.join(lines));return 0


if __name__=='__main__':raise SystemExit(main())
