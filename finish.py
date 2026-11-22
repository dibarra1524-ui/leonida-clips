#!/usr/bin/env python3
"""finish.py - used by the scheduled posting run.
  python3 finish.py next              list queued clips, best first, and extract review frames for each
  python3 finish.py skip ID "reason"  drop a clip that failed review
  python3 finish.py make ID --hook "..." --caption "..."   burn the hook, write out/ID.mp4 and post.json
  python3 finish.py posted ID MEDIA_ID   record a successful post
"""
import argparse, datetime as dt, json, os, subprocess, sys, tempfile
import learn
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
RAW = 'https://raw.githubusercontent.com/dibarra1524-ui/leonida-clips/main/'
W, H = 1080, 1920


def load(p, d):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p))
    except ValueError:
        return d


def save(p, o):
    json.dump(o, open(p, 'w'), indent=1)


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def queue():
    items = [load('queue/' + f, None) for f in os.listdir('queue') if f.endswith('.json')]
    items = [m for m in items if m and os.path.exists('queue/%s.mp4' % m['id'])]
    # campaign clips first (they pay per view), then by rising score
    return sorted(items, key=lambda m: (m.get('campaign') is None, -m['score']))


def cmd_next(a):
    items = queue()
    if not items:
        print('QUEUE EMPTY')
        return
    tr = learn.load().get('trend', {})
    if tr.get('titles'):
        print(json.dumps({'trending_shorts_titles': tr['titles'][:10],
                          'hot_topics': sorted(tr.get('topics', {}), key=lambda k: -tr['topics'][k])[:5]}))
    for m in items[:a.n]:
        d = os.path.join(tempfile.gettempdir(), 'review', m['id'])
        os.makedirs(d, exist_ok=True)
        dur = m.get('kept') or m['duration']
        frames = []
        for i, t in enumerate([0.5, 2, 4, 6.5, dur / 2, max(dur - 1.5, 0)]):
            p = os.path.join(d, 'f%d_%.1fs.jpg' % (i, t))
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(t), '-i', 'queue/%s.mp4' % m['id'],
                            '-frames:v', '1', '-vf', 'scale=540:-2', p])
            frames.append(p)
        out = {k: m.get(k) for k in ('id', 'streamer', 'login', 'platform', 'credit', 'title', 'views', 'duration', 'kept', 'layout', 'campaign', 'action', 'transcript')}
        g = m.get('genes', {})
        out['hook_style'] = g.get('hook_style', 'streamer')
        out['hook_rule'] = learn.HOOK_STYLES.get(out['hook_style'], '')
        out['frames'] = frames
        print(json.dumps(out))


def cmd_skip(a):
    for ext in ('.mp4', '.json'):
        p = 'queue/%s%s' % (a.id, ext)
        if os.path.exists(p):
            os.remove(p)
    sk = load('state/skipped.json', [])
    sk.append({'id': a.id, 'reason': a.reason, 'at': now()})
    save('state/skipped.json', sk[-500:])
    print('skipped', a.id)


def hook_ass(text, y, path, case='upper'):
    text = text.upper() if case == 'upper' else ' '.join(w if w.isupper() and len(w) > 1 else w.capitalize() for w in text.split())
    words, lines, cur = text.split(), [], ''
    for w in words:
        if len(cur) + len(w) + 1 > 20 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + ' ' + w).strip()
    lines.append(cur)
    txt = '\\N'.join(l.replace('{', '').replace('}', '') for l in lines[:3])
    open(path, 'w').write(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: Hook,DejaVu Sans,64,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,14,0,5,60,60,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,9:00:00.00,Hook,,0,0,0,,{\\pos(%d,%d)}%s\n" % (W, H, W // 2, y, txt))


def cmd_make(a):
    m = load('queue/%s.json' % a.id, None)
    if not m:
        sys.exit('not in queue: ' + a.id)
    os.makedirs('out', exist_ok=True)
    ass = os.path.join(tempfile.mkdtemp(), 'hook.ass')
    hook_ass(a.hook, 640 if m['layout'] == 'cam' else 440, ass, m.get('genes', {}).get('hook_case', 'upper'))
    dst = 'out/%s.mp4' % a.id
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', 'queue/%s.mp4' % a.id, '-vf', "subtitles='%s'" % ass,
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-c:a', 'copy', '-movflags', '+faststart', dst], check=True)
    for ext in ('.mp4', '.json'):
        os.remove('queue/%s%s' % (a.id, ext))
    m.update(hook=a.hook, caption=a.caption, file=dst, url=RAW + dst)
    save('post.json', m)
    print(json.dumps({k: m[k] for k in ('id', 'file', 'url', 'caption', 'hook', 'streamer')}))


def cmd_posted(a):
    m = load('post.json', {})
    posted = load('state/posted.json', [])
    posted.append({'id': a.id, 'media_id': a.media_id, 'posted_at': now(), 'streamer': m.get('login'),
                   'hook': m.get('hook'), 'caption': m.get('caption'), 'campaign': m.get('campaign'),
                   'clip_url': m.get('clip_url'), 'url': m.get('url')})
    save('state/posted.json', posted)
    hour = dt.datetime.now(dt.timezone(dt.timedelta(hours=-7))).hour      # Tucson time
    learn.record(m, a.media_id, now(), hour)
    # queue the same video as a YouTube Short (the push runs .github/workflows/shorts.yml)
    os.makedirs('shorts', exist_ok=True)
    hook = (m.get('hook') or 'GTA 6').strip()
    save('shorts/%s.json' % a.id, {'id': a.id, 'file': m.get('file') or 'out/%s.mp4' % a.id, 'streamer': m.get('login'),
                                   'title': (hook + ' #gta6 #shorts')[:100],
                                   'description': (m.get('caption') or '') + ('\n\nClip: ' + m['clip_url'] if m.get('clip_url') else ''),
                                   'created': now()[:10]})
    print('recorded', a.id, a.media_id)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('next'); s.add_argument('-n', type=int, default=4); s.set_defaults(f=cmd_next)
    s = sp.add_parser('skip'); s.add_argument('id'); s.add_argument('reason'); s.set_defaults(f=cmd_skip)
    s = sp.add_parser('make'); s.add_argument('id'); s.add_argument('--hook', required=True)
    s.add_argument('--caption', required=True); s.set_defaults(f=cmd_make)
    s = sp.add_parser('posted'); s.add_argument('id'); s.add_argument('media_id'); s.set_defaults(f=cmd_posted)
    a = p.parse_args()
    a.f(a)
