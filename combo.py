#!/usr/bin/env python3
"""combo.py - builds "top moments" compilations from queued clips of ONE campaign streamer.

Single moments get posted by dozens of other clippers too, so they get buried as duplicates.
A ranked compilation ("#3 ... #2 ... #1") of one streamer's best recent clips is our own edit.

  python3 combo.py list            streamers with 2+ queued campaign clips (candidates for a combo)
  python3 combo.py build LOGIN     stitch that streamer's best 2-3 queued clips into one ranked video,
                                   written to queue/COMBO-<login>-<stamp>.mp4 + .json; it then goes
                                   through finish.py next / make / posted like any other clip.
                                   The clips used are removed from the queue (recorded in state/combos.json).
Each part is kept to at most PART_MAX seconds; the best clip is shown last as #1.
"""
import datetime as dt, json, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
PART_MAX = 14.0
MAX_PARTS = 3
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def load(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def queued():
    out = []
    for f in os.listdir('queue'):
        if f.endswith('.json') and not f.startswith('COMBO-'):
            m = load('queue/' + f, None)
            if m and m.get('campaign') and os.path.exists('queue/%s.mp4' % m['id']):
                out.append(m)
    return out


def groups():
    g = {}
    for m in queued():
        g.setdefault(m['login'].lower(), []).append(m)
    return {k: sorted(v, key=lambda m: -m['score']) for k, v in g.items() if len(v) >= 2}


def dur(p):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                                capture_output=True, text=True).stdout or 0)


def build(login):
    g = groups().get(login.lower())
    if not g:
        sys.exit('need 2+ queued campaign clips for ' + login)
    parts = g[:MAX_PARTS]                     # best first
    order = list(reversed(parts))             # play worst -> best, best is #1 at the end
    n = len(order)
    tmp = tempfile.mkdtemp()
    segs = []
    for i, m in enumerate(order):
        rank = n - i
        src = 'queue/%s.mp4' % m['id']
        t = min(PART_MAX, dur(src))
        seg = os.path.join(tmp, 'p%d.mp4' % i)
        badge = ("drawtext=fontfile=%s:text='#%d':fontsize=110:fontcolor=white:borderw=8:bordercolor=black:"
                 "x=w-tw-50:y=60" % (FONT, rank))
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src, '-t', str(t),
                        '-vf', 'scale=1080:1920,setsar=1,fps=30,' + badge,
                        '-af', 'aresample=48000,aformat=channel_layouts=stereo',
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-b:a', '160k', seg], check=True, timeout=600)
        segs.append(seg)
    lst = os.path.join(tmp, 'list.txt')
    open(lst, 'w').write(''.join("file '%s'\n" % s for s in segs))
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%m%d%H%M')
    cid = 'COMBO-%s-%s' % (login.lower(), stamp)
    dst = 'queue/%s.mp4' % cid
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                    '-movflags', '+faststart', dst], check=True)
    best = parts[0]
    meta = dict(best)
    total = dur(dst)
    meta.update(id=cid, title='%d best %s GTA 6 moments: ' % (n, best['streamer']) + ' | '.join(m['title'] for m in order),
                transcript=' || '.join(m.get('transcript', '') for m in order)[:600],
                duration=round(total), kept=round(total, 1), views=sum(m.get('views', 0) for m in parts),
                score=best['score'] + 1000, combo=[m['id'] for m in order], hot=False,
                queued_at=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                genes=dict(best.get('genes', {}), format='combo'),
                clip_url=best.get('clip_url'))
    json.dump(meta, open('queue/%s.json' % cid, 'w'), indent=1)
    for m in parts:
        for ext in ('.mp4', '.json'):
            p = 'queue/%s%s' % (m['id'], ext)
            if os.path.exists(p):
                os.remove(p)
    log = load('state/combos.json', [])
    log.append({'id': cid, 'streamer': login.lower(), 'parts': meta['combo'], 'at': meta['queued_at']})
    json.dump(log[-200:], open('state/combos.json', 'w'), indent=1)
    print(json.dumps({'id': cid, 'parts': n, 'seconds': round(total, 1), 'campaign': meta['campaign']}))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'list':
        for k, v in sorted(groups().items(), key=lambda kv: -len(kv[1])):
            print(json.dumps({'login': k, 'clips': len(v), 'campaign': v[0]['campaign'],
                              'titles': [m['title'] for m in v[:MAX_PARTS]]}))
    elif cmd == 'build':
        build(sys.argv[2])
