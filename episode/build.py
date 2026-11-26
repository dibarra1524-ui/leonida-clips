"""episode/build.py - daily long-form episode, runs in GitHub Actions (needs TWITCH_CLIENT_ID/SECRET).

Picks the day's most-viewed GTA VI clips (views per hour), cuts them into one 16:9 compilation:
  - no intro: opens on the best clip of the day, second best goes last
  - total length follows the winning compilations in research/meta.json (target_len), else 10 min
  - every clip gets a one-line label (episode/labels/<date>.json if the review run wrote one, else the clip title)
    and the streamer's handle; every streamer is credited with a link in the description
Writes episode/<date>/ (plan.json, description.txt, review frames, 3 thumbnails) and out/episode.mp4
(the workflow uploads that to a GitHub release, it is too big for git).

  python episode/build.py [--date YYYY-MM-DD] [--relabel]   (--relabel reuses the plan's clips, new labels)
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, ROOT, os.path.join(ROOT, 'clipper')]
os.chdir(ROOT)
import fetch, thumbs   # noqa: E402  (fetch gives the Twitch client and spoiler filter)

TZ = dt.timezone(dt.timedelta(hours=-7))      # Tucson
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
MIN_VIEWS = int(os.environ.get('EP_MIN_VIEWS', '150'))
PER_STREAMER = int(os.environ.get('EP_PER_STREAMER', '5'))
SKIP_WORDS = re.compile(r'strip|club|dance|thong|cutscene|cinematic|glitch|exploit|money glitch|dupe|infinite money|mod menu|cheat', re.I)


def load(p, d):
    return json.load(open(p)) if os.path.exists(p) else d


def save(p, o):
    json.dump(o, open(p, 'w'), indent=1)


def target_len():
    t = load('research/meta.json', {}).get('target_len') or 600
    return max(420, min(int(t), 900))


def clean_label(title):
    t = re.sub(r'https?://\S+|[@#]\S+|\b(xd|lol|lmao|kekw|omegalul|pog\w*)\b', '', title, flags=re.I)
    t = ''.join(ch for ch in t if ch.isascii() or ch.isalpha())
    t = re.sub(r'\s+', ' ', t).strip(' -|:!.')
    return (t[:1].upper() + t[1:] if t[:1].islower() and not t[1:2].isupper() else t)[:48] or 'GTA 6'


def pick(date):
    cfg = fetch.load('streamers.json', {'streamers': []})
    ok = {s['login'].lower() for s in cfg['streamers'] if s.get('approved')}
    blocked = {b.lower() for b in cfg.get('blocked', [])}
    used = set(load('state/episodes.json', {}).get('clip_ids', []))
    tw = fetch.Twitch()
    gid = tw.get('games', {'name': fetch.GAME})[0]['id']
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=26)).strftime('%Y-%m-%dT%H:%M:%SZ')
    clips = {}
    users = {}
    logins = sorted(ok)
    for i in range(0, len(logins), 100):
        for u in tw.get('users', {'login': logins[i:i + 100]}):
            users[u['id']] = u
    for uid in users:
        for c in tw.get('clips', {'broadcaster_id': uid, 'started_at': since, 'first': 40}):
            if c['game_id'] == gid:
                clips[c['id']] = c
    good = []
    for c in clips.values():
        login = c['broadcaster_name'].lower()
        if (c['id'] in used or login in blocked or login not in ok or (c.get('language') or 'en')[:2] not in fetch.LANGS
                or c['view_count'] < MIN_VIEWS or not (8 <= c['duration'] <= 60)
                or fetch.BAD_TITLE.search(c['title']) or SKIP_WORDS.search(c['title'])):
            continue
        c['_vph'] = fetch.score(c, now)
        good.append(c)
    good.sort(key=lambda c: -c['_vph'])
    picked, per, moments, total = [], {}, set(), 0
    for c in good:
        m = '%s:%d' % (c.get('video_id'), (c.get('vod_offset') or 0) // 45) if c.get('video_id') else None
        b = c['broadcaster_id']
        if (m and m in moments) or per.get(b, 0) >= PER_STREAMER:
            continue
        picked.append(c)
        per[b] = per.get(b, 0) + 1
        moments.add(m)
        total += c['duration']
        if total >= target_len() * 1.8:
            break
    if len(picked) < 6:
        sys.exit('only %d usable clips today, not enough for an episode' % len(picked))
    return [{'id': c['id'], 'url': c['url'], 'title': c['title'], 'streamer': c['broadcaster_name'],
             'login': c['broadcaster_name'].lower(), 'views': c['view_count'], 'vph': round(c['_vph'], 1),
             'duration': c['duration'], 'created_at': c['created_at']} for c in picked]


def download(c, tmp):
    raw = os.path.join(tmp, c['id'] + '.mp4')
    if not os.path.exists(raw):
        subprocess.run(['yt-dlp', '-q', '-f', 'best[height<=1080]/best', '-o', raw, c['url']], check=True, timeout=300)
    return raw


_NUDE = None
EXPOSED = {'BUTTOCKS_EXPOSED', 'FEMALE_BREAST_EXPOSED', 'FEMALE_GENITALIA_EXPOSED', 'MALE_GENITALIA_EXPOSED', 'ANUS_EXPOSED'}


def screen(raw):
    """First-pass content filter (NudeNet on 8 frames). Catches nudity and strip-club scenes; the review run
    still checks every frame, this only keeps the obvious ones out of the cut and the thumbnails."""
    global _NUDE
    try:
        if _NUDE is None:
            from nudenet import NudeDetector
            _NUDE = NudeDetector()
    except Exception as e:
        print('nudenet unavailable:', e)
        return None
    import cv2
    cap = cv2.VideoCapture(raw)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 300
    strip = 0
    for i in range(8):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (0.06 + 0.88 * i / 7)))
        ok, f = cap.read()
        if not ok:
            continue
        p = raw + '.screen.jpg'
        cv2.imwrite(p, f)
        hits = {d['class'] for d in _NUDE.detect(p) if d['score'] >= 0.45}
        if hits & EXPOSED:
            cap.release()
            return 'nudity (%s)' % ', '.join(sorted(hits & EXPOSED))
        if 'FEMALE_BREAST_COVERED' in hits and 'BELLY_EXPOSED' in hits:
            strip += 1
    cap.release()
    return 'strip club / lingerie' if strip >= 2 else None


def segment(c, label, dst, tmp, right=False, handle_top=False):
    raw = download(c, tmp)
    lx = 'w-tw-60' if right else '60'          # labels "_right": top-right label for clips with a top-left facecam
    hy = '170' if handle_top else 'h-110'      # "_handle_top": handle box under the label for clips with a bottom-left facecam
    lf, hf = os.path.join(tmp, c['id'] + '.label'), os.path.join(tmp, c['id'] + '.handle')
    open(lf, 'w').write(''.join(ch for ch in label.upper() if ch.isascii() or ch.isalpha()))
    open(hf, 'w').write('twitch.tv/' + c['login'])
    vf = ("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,"
          "drawtext=fontfile=%s:textfile=%s:fontsize=56:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=20:"
          "x=%s:y=60:enable='lt(t,6)',"
          "drawtext=fontfile=%s:textfile=%s:fontsize=36:fontcolor=white@0.95:box=1:boxcolor=0x9146FF@0.85:boxborderw=12:"
          "x=%s:y=%s" % (FONT, lf, lx, FONT, hf, lx, hy))
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', raw, '-vf', vf,
                    '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000', '-ac', '2',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-r', '30',
                    '-c:a', 'aac', '-b:a', '160k', dst], check=True, timeout=600)
    return raw


def dur(p):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                                capture_output=True, text=True).stdout.strip() or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.datetime.now(TZ).strftime('%Y-%m-%d'))
    ap.add_argument('--relabel', action='store_true')
    a = ap.parse_args()
    d = 'episode/' + a.date
    os.makedirs(d + '/frames', exist_ok=True)
    plan = load(d + '/plan.json', None)
    clips = plan['clips'] if (a.relabel and plan) else pick(a.date)
    labels = load('episode/labels/%s.json' % a.date, {})
    drop = set(labels.get('_drop', []))          # clips the review run threw out
    clips = [c for c in clips if c['id'] not in drop]
    if labels.get('_first'):                     # the review run can pick a stronger opener
        clips.sort(key=lambda c: c['id'] != labels['_first'])
    if labels.get('_order'):                     # full running order (keeps same-streamer clips apart)
        pos = {k: i for i, k in enumerate(labels['_order'])}
        clips.sort(key=lambda c: pos.get(c['id'], len(pos)))
    tmp = tempfile.mkdtemp()
    if not (a.relabel and plan):
        kept, total, screened = [], 0, []
        for c in clips:
            if total >= target_len():
                break
            try:
                why = screen(download(c, tmp))
            except Exception as e:
                why = 'download failed: %s' % str(e)[:120]
            if why:
                screened.append({'id': c['id'], 'streamer': c['streamer'], 'title': c['title'], 'why': why})
                print('screened out', c['id'], c['streamer'], why)
                continue
            kept.append(c)
            total += c['duration']
        if len(kept) < 6:
            sys.exit('only %d clean clips today, not enough for an episode' % len(kept))
        clips = [kept[0]] + kept[2:] + [kept[1]]      # best first, second best last
    else:
        screened = plan.get('screened_out', [])
    segs, t, chapters, raws = [], 0.0, [], {}
    for i, c in enumerate(clips):
        c['label'] = labels.get(c['id']) or (c.get('label') if a.relabel else None) or clean_label(c['title'])
        s = os.path.join(tmp, 'seg%03d.mp4' % i)
        try:
            raws[c['id']] = segment(c, c['label'], s, tmp, c['id'] in set(labels.get('_right', [])),
                                  c['id'] in set(labels.get('_handle_top', [])))
        except Exception as e:
            print('dropped', c['id'], e)
            c['dropped'] = str(e)[:200]
            continue
        c['start'] = round(t, 1)
        chapters.append((t, '%s - %s' % (c['label'], c['streamer'])))
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(min(2.5, dur(s) / 2)), '-i', s, '-frames:v', '1',
                        '-vf', 'scale=640:-2', '%s/frames/%02d.jpg' % (d, i)])
        t += dur(s)
        segs.append(s)
    clips = [c for c in clips if not c.get('dropped')]
    lst = os.path.join(tmp, 'list.txt')
    open(lst, 'w').write(''.join("file '%s'\n" % s for s in segs))
    os.makedirs('out', exist_ok=True)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                    '-movflags', '+faststart', 'out/episode.mp4'], check=True)
    # YouTube chapter rules (0:00 first, 3+ entries, 10 s+ each), else the block silently does not render
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'yt'))
    import chapters as ytch
    chapters = ytch.lines(chapters, t)
    credits = sorted({c['login'] for c in clips})
    desc = ('The best GTA 6 moments from the last 24 hours.\n\n' + ('\n'.join(chapters) + '\n' if chapters else '') +
            '\n\nCredits - go follow them:\n' + '\n'.join('https://twitch.tv/' + l for l in credits) +
            '\n\nClips:\n' + '\n'.join(c['url'] for c in clips) +
            '\n\nAll clips belong to the streamers credited above. Want a clip removed? Comment and it comes down.\n'
            '#gta6 #gtavi #gta6clips')
    open(d + '/description.txt', 'w').write(desc)
    thumb_files = thumbs.make(clips, raws, d)
    ep_no = len(load('state/episodes.json', {}).get('episodes', [])) + (0 if a.relabel else 1)
    plan = {'date': a.date, 'episode': ep_no, 'length_s': round(t), 'target_s': target_len(), 'clips': clips,
            'thumbnails': thumb_files, 'screened_out': screened, 'default_title': 'GTA 6 Best Clips of the Day #%d' % ep_no,
            'release': 'ep-' + a.date, 'built_at': fetch.dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    save(d + '/plan.json', plan)
    st = load('state/episodes.json', {'clip_ids': [], 'episodes': []})
    if not a.relabel:
        st['clip_ids'] = (st['clip_ids'] + [c['id'] for c in clips])[-4000:]
        st['episodes'].append({'date': a.date, 'status': 'built', 'length_s': round(t), 'clips': len(clips)})
    save('state/episodes.json', st)
    print('episode %s: %d clips, %d:%02d' % (a.date, len(clips), int(t) // 60, int(t) % 60))


if __name__ == '__main__':
    main()
