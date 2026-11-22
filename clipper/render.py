"""render.py - turns a 16:9 stream clip into a 1080x1920 Short with word-by-word captions.

Layouts
  cam : facecam on top (1080x640), gameplay below (1080x1280)
  full: gameplay zoomed in the middle over a blurred copy of itself
Captions are burned from word timestamps (faster-whisper). The hook is added later by finish.py.
"""
import json, os, subprocess, sys, tempfile

W, H = 1080, 1920
TOP_H = 640                    # facecam panel height in the cam layout
FONT = 'DejaVu Sans'


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                          'stream=width,height:format=duration', '-of', 'json', path],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j['streams'][0]
    return s['width'], s['height'], float(j['format']['duration'])


YUNET_URL = 'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
YUNET = os.path.expanduser('~/.cache/leonida/yunet.onnx')


def face_detector():
    """YuNet (accurate, handles angled faces and headsets) with the Haar cascade as a fallback."""
    import cv2
    try:
        if not os.path.exists(YUNET) or os.path.getsize(YUNET) < 100_000:
            import urllib.request
            os.makedirs(os.path.dirname(YUNET), exist_ok=True)
            urllib.request.urlretrieve(YUNET_URL, YUNET + '.part')
            os.replace(YUNET + '.part', YUNET)
        det = cv2.FaceDetectorYN.create(YUNET, '', (320, 320), 0.7, 0.3, 5000)

        def run(frame):
            det.setInputSize((frame.shape[1], frame.shape[0]))
            _, faces = det.detect(frame)
            return [] if faces is None else [tuple(int(v) for v in r[:4]) for r in faces]
        return run
    except Exception as e:
        print('YuNet unavailable, using Haar:', e)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        return lambda f: cascade.detectMultiScale(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 1.1, 6,
                                                  minSize=(int(f.shape[1] * 0.035),) * 2)


def find_cam(path, dur):
    """Look for one face that stays put in a corner/edge region across most sampled frames.
    Returns [x, y, w, h] as fractions of the frame, or None."""
    import cv2
    detect = face_detector()
    cap = cv2.VideoCapture(path)
    hits, n = [], 10
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (dur * (i + 0.5) / n) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        fh, fw = frame.shape[:2]
        for (x, y, w, h) in detect(frame):
            if w < fw * 0.03:
                continue
            cx = (x + w / 2) / fw
            if 0.3 < cx < 0.7 or w / fw > 0.25:   # facecams sit near the sides; skip mid-screen game faces
                continue
            hits.append(((x + w / 2) / fw, (y + h / 2) / fh, w / fw))
    cap.release()
    if len(hits) < n * 0.5:
        return None
    # largest cluster of hits that agree within 4% of the frame
    best = []
    for a in hits:
        grp = [b for b in hits if abs(a[0] - b[0]) < 0.04 and abs(a[1] - b[1]) < 0.06]
        if len(grp) > len(best):
            best = grp
    if len(best) < n * 0.5:
        return None
    cx = sorted(b[0] for b in best)[len(best) // 2]
    cy = sorted(b[1] for b in best)[len(best) // 2]
    fwid = sorted(b[2] for b in best)[len(best) // 2]
    # box around the face with the panel's 27:16 shape (in 16:9 frame fractions)
    bw = min(0.45, fwid * 3.4)
    bh = bw * (16 / 27) * (16 / 9)
    x = min(max(cx - bw / 2, 0), 1 - bw)
    y = min(max(cy - bh * 0.45, 0), 1 - bh)
    return [round(x, 4), round(y, 4), round(bw, 4), round(bh, 4)]


def cam_has_face(path, dur, cam, n=6):
    """True if a face shows up inside the saved facecam box in at least half the sampled frames."""
    import cv2
    detect = face_detector()
    cap = cv2.VideoCapture(path)
    hits = 0
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (dur * (i + 0.5) / n) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        fh, fw = frame.shape[:2]
        x, y, w, h = int(cam[0] * fw), int(cam[1] * fh), int(cam[2] * fw), int(cam[3] * fh)
        crop = frame[y:y + h, x:x + w]
        if crop.size and any(face_centered(f, w, h) for f in detect(crop)):
            hits += 1
    cap.release()
    return hits >= n / 2


def face_centered(f, bw, bh):
    """Face box (x, y, w, h) inside a crop of size bw x bh: centre in the middle 60% and not touching an edge."""
    x, y, w, h = f[:4]
    cx, cy = (x + w / 2) / bw, (y + h / 2) / bh
    return 0.2 < cx < 0.8 and 0.2 < cy < 0.8 and x > 2 and y > 2 and x + w < bw - 2 and y + h < bh - 2


BAD = __import__('re').compile(r"\b(fuck\w*|shit\w*|bitch\w*|pussy|dick\w*|cunt\w*|motherf\w*|asshole\w*)", __import__('re').I)


def censor(word):
    return BAD.sub(lambda m: m.group(0)[0] + '*' * (len(m.group(0)) - 1), word)


def transcribe(path):
    """Word timestamps. Returns [(start, end, word)]. Empty list if whisper is unavailable."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    model = WhisperModel(os.environ.get('WHISPER_MODEL', 'base.en'), device='cpu', compute_type='int8')
    segs, _ = model.transcribe(path, word_timestamps=True, vad_filter=True)
    words = []
    for s in segs:
        for w in (s.words or []):
            t = w.word.strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def ts(t):
    t = max(t, 0)
    return '%d:%02d:%05.2f' % (t // 3600, (t % 3600) // 60, t % 60)


def captions_ass(words, y, path, size=84):
    """2-3 word chunks, current chunk only, yellow highlight on the word being spoken."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Cap,%s,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,5,40,40,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") % (W, H, FONT, size)
    lines, chunk = [], []
    for w in words:
        chunk.append(w)
        if len(chunk) == 3 or w[2][-1:] in '.?!,' or (len(chunk) == 2 and len(chunk[0][2] + w[2]) > 14):
            lines.append(chunk)
            chunk = []
    if chunk:
        lines.append(chunk)
    ev = []
    for i, ch in enumerate(lines):
        end_chunk = lines[i + 1][0][0] if i + 1 < len(lines) else ch[-1][1] + 0.4
        end_chunk = min(end_chunk, ch[-1][1] + 0.8)
        for j, (s, e, _) in enumerate(ch):
            e2 = ch[j + 1][0] if j + 1 < len(ch) else end_chunk
            txt = ' '.join(('{\\c&H00E5FF&}%s{\\c&HFFFFFF&}' if k == j else '%s') % censor(clean(c[2])).upper()
                           for k, c in enumerate(ch))
            ev.append('Dialogue: 0,%s,%s,Cap,,0,0,0,,{\\pos(%d,%d)}%s' % (ts(s), ts(e2), W // 2, y, txt))
    open(path, 'w').write(head + '\n'.join(ev) + '\n')


def clean(t):
    return t.replace('{', '').replace('}', '').replace('\\', '')


def loudness(path):
    """RMS level (dB) per 0.25 s of the clip's audio, as a list. Empty if there is no audio."""
    import numpy as np
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = len(x) // 2000
    if n == 0:
        return []
    r = np.sqrt((x[:n * 2000].reshape(n, 2000) ** 2).mean(1) + 1e-9)
    return list(20 * np.log10(r))


def best_window(levels, dur, max_len, ending='runon'):
    """(start, length) in seconds. Picks the stretch with the most energy (mean + peak), starts just
    before the first spike in it, and lets it run on past a peak at the end so the payoff isn't cut."""
    step = 0.25
    if not levels:
        return 0.0, min(dur, max_len)
    import numpy as np
    lv = np.array(levels)
    base = np.median(lv)
    ex = np.clip(lv - base, 0, None)
    win = int(min(max_len, dur) / step)
    if len(lv) <= win:
        start = 0
    else:
        sc = [ex[i:i + win].mean() + 0.5 * ex[i:i + win].max() for i in range(len(lv) - win + 1)]
        start = int(np.argmax(sc))
    # lead-in: skip dead air at the head, but keep 0.75 s before the first spike
    spikes = np.where(ex[start:start + win] >= max(6.0, 0.6 * np.percentile(ex, 90)))[0]
    if len(spikes) and spikes[0] * step > 1.5:
        start += max(int(spikes[0] - 3), 0)
    end = min(start + win, len(lv))
    if ending == 'hard':
        # hard cut: end 0.5 s after the last big spike in the window (no fade-out tail), keep at least 6 s
        big = np.where(ex[start:end] >= max(6.0, np.percentile(ex, 85)))[0]
        if len(big):
            end = max(start + 24, min(end, start + int(big[-1]) + 2))
    else:
        # run-on: if the last 1.5 s is loud, extend up to 3 s (within the clip)
        tail = ex[max(end - 6, 0):end]
        if len(tail) and tail.max() > max(6.0, np.percentile(ex, 85)):
            end = min(end + 12, len(lv))
    return round(start * step, 2), round((end - start) * step, 2)


def cold_open(src, dur):
    """MrBeast-style cold open: flash the loudest ~1.2 s (the payoff) first, then play the clip from the start.
    Returns (new src, new duration). Leaves the clip unchanged if it has no audio or is too short."""
    lv = loudness(src)
    if not lv or dur < 6:
        return src, dur
    import numpy as np
    peak = int(np.argmax(np.array(lv))) * 0.25
    a = max(0.0, min(peak - 0.5, dur - 1.2))
    b = a + 1.2
    if a < 1.5:          # payoff is already at the start: nothing to tease
        return src, dur
    out = os.path.join(tempfile.mkdtemp(), 'cold.mp4')
    fc = ('[0:v]trim=%.2f:%.2f,setpts=PTS-STARTPTS[v0];[0:a]atrim=%.2f:%.2f,asetpts=PTS-STARTPTS[a0];'
          '[0:v]setpts=PTS-STARTPTS[v1];[0:a]asetpts=PTS-STARTPTS[a1];'
          '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]') % (a, b, a, b)
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src, '-filter_complex', fc, '-map', '[v]', '-map', '[a]',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '16', '-c:a', 'aac', '-b:a', '192k', out])
    if r.returncode != 0:
        return src, dur
    return out, probe(out)[2]


def punch_at(src, dur):
    """Start time (s) of a 0.6 s punch-in on the loudest moment, or None (no audio / too short / flat audio)."""
    lv = loudness(src)
    if not lv or dur < 3:
        return None
    import numpy as np
    a = np.array(lv)
    i = int(np.argmax(a))
    if a[i] - np.median(a) < 6:        # nothing stands out: a zoom on a random spot looks like a glitch
        return None
    t = max(0.0, i * 0.25 - 0.1)      # land the zoom on the hit, not after it
    return round(min(t, max(dur - 0.6, 0)), 2)


def punch_chain(label, out, w, h, t, z=1.15, d=0.6):
    """Filter text: scale [label] to w x h as [out], zoomed z-x (centre) between t and t+d."""
    zw, zh = int(w * z) // 2 * 2, int(h * z) // 2 * 2
    return ('[%s]scale=%d:%d,split=2[%s_n][%s_z];[%s_z]scale=%d:%d,crop=%d:%d[%s_zc];'
            "[%s_n][%s_zc]overlay=0:0:enable='between(t,%.2f,%.2f)'[%s];") % (
        label, w, h, out, out, out, zw, zh, w, h, out, out, out, t, t + d, out)


def render(src, dst, cam=None, layout='auto', credit='', genes=None):
    """Returns (cam box used or None, transcript, layout, seconds kept)."""
    genes = genes or {}
    sw, sh, dur = probe(src)
    max_len = float(genes.get('max_len', 60))
    if genes.get('trim', 'smart') == 'smart' or dur > max_len:
        st, ln = best_window(loudness(src), dur, max_len, genes.get('ending', 'runon')) if genes.get('trim', 'smart') == 'smart' else (0.0, max_len)
        if st > 0.1 or ln < dur - 0.3:
            cut = os.path.join(tempfile.mkdtemp(), 'cut.mp4')
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(st), '-i', src, '-t', str(ln), '-c:v', 'libx264',
                            '-preset', 'ultrafast', '-crf', '16', '-c:a', 'aac', '-b:a', '192k', cut], check=True)
            src = cut
            sw, sh, dur = probe(src)
    if genes.get('opener') == 'cold':
        src, dur = cold_open(src, dur)
    if layout == 'auto':
        if cam and not cam_has_face(src, dur, cam):
            print('saved facecam box has no face in this clip, re-detecting')
            cam = None
        if not cam:
            cam = find_cam(src, dur)
            if cam and not cam_has_face(src, dur, cam):
                print('detected facecam box does not frame the face cleanly, using full layout')
                cam = None
        layout = 'cam' if cam else 'full'
    words = transcribe(src)
    action = action_score(src, ' '.join(w[2] for w in words), genes.get('_title', ''))
    if genes.get('_min_action') is not None and action[0] < genes['_min_action']:
        return None, ' '.join(w[2] for w in words), 'rejected', round(dur, 2), action
    tmp = tempfile.mkdtemp()
    ass = os.path.join(tmp, 'cap.ass')
    low = genes.get('cap_pos', 'low') == 'low'
    cap_y = (1480 if low else TOP_H + 140) if layout == 'cam' else (1500 if low else 1180)
    captions_ass(words, cap_y, ass, int(genes.get('cap_size', 84)))
    pt = punch_at(src, dur) if genes.get('punch') == 'on' else None
    base = '[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,split=2[a][b];'
    if layout == 'cam':
        x, y, bw, bh = cam
        cx, cy, cw, ch = int(x * 1920), int(y * 1080), int(bw * 1920), int(bh * 1080)
        gw = int(1080 * 1080 / 1280) // 2 * 2
        f = (base + '[a]crop=%d:%d:%d:%d,scale=%d:%d[top];' % (cw, ch, cx, cy, W, TOP_H) +
             ('[b]crop=%d:1080:%d:0,scale=%d:%d[bot];' % (gw, (1920 - gw) // 2, W, H - TOP_H) if pt is None else
              '[b]crop=%d:1080:%d:0[gp];' % (gw, (1920 - gw) // 2) + punch_chain('gp', 'bot', W, H - TOP_H, pt)) +
             '[top][bot]vstack,drawbox=y=%d:w=iw:h=6:color=black@1:t=fill' % (TOP_H - 3))
    else:
        f = (base + '[a]crop=608:1080:656:0,scale=%d:%d,boxblur=24:2,eq=brightness=-0.12[bg];' % (W, H) +
             ('[b]crop=1500:1080:210:0,scale=%d:%d[fg];' % (W, int(W * 1080 / 1500) // 2 * 2) if pt is None else
              '[b]crop=1500:1080:210:0[gp];' + punch_chain('gp', 'fg', W, int(W * 1080 / 1500) // 2 * 2, pt)) +
             '[bg][fg]overlay=0:(H-h)/2')
    if credit:
        f += ",drawtext=font='%s':text='%s':fontsize=34:fontcolor=white@0.85:borderw=3:bordercolor=black@0.8:x=36:y=%d" % (
            FONT, credit.replace("'", '').replace(':', ''), (TOP_H + 100) if layout == 'cam' else 1340)
    f += ",subtitles='%s'[v]" % ass
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', src, '-filter_complex', f, '-map', '[v]', '-map', '0:a?',
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-r', '30',
           '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11',   # streamers' mic levels vary wildly; -14 LUFS matches Reels/Shorts
           '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-movflags', '+faststart', dst]
    subprocess.run(cmd, check=True)
    return (cam if layout == 'cam' else None), ' '.join(w[2] for w in words), layout, round(dur, 2), action


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    print(render(src, dst, layout=os.environ.get('LAYOUT', 'auto'), credit=os.environ.get('CREDIT', ''), genes=json.loads(os.environ.get('GENES', '{}'))))


HYPE_WORDS = __import__('re').compile(
    r"\b(no way|oh my god|omg|what+|wait|bro+|let'?s go+|holy|insane|crazy|yo+|wtf|dude|nah+|run+|go go|"
    r"help|oh no|stop|why|dead|died|lmao|lol|bruh|ha(ha)+|f\*+|s\*+|b\*+)\b", __import__('re').I)


def action_score(path, transcript, title=''):
    """0..1 'is this high action or funny' score from the finished clip.
    audio  : how spiky the loudness is (yelling, crashes, explosions)
    motion : how much the picture changes frame to frame (chases, flying, fights)
    talk   : hype words and how fast the streamer is talking (reactions)
    Returns (score, parts) so the learner can see which part mattered."""
    import cv2, numpy as np
    lv = loudness(path)
    if lv:
        a = np.array(lv)
        spread = np.percentile(a, 95) - np.percentile(a, 50)
        spikes = (a - np.median(a) >= 10).mean()
        audio = 0.6 * min(1, spread / 18) + 0.4 * min(1, spikes * 5)
    else:
        audio = 0.0
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(int(fps / 4), 1)
    prev, diffs, i = None, [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, f = cap.retrieve()
            if ok:
                g = cv2.cvtColor(cv2.resize(f, (90, 160)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                if prev is not None:
                    diffs.append(float(np.abs(g - prev).mean()))
                prev = g
        i += 1
    cap.release()
    motion = min(1, (np.percentile(diffs, 75) if diffs else 0) / 22)
    words = transcript.split()
    dur = max(probe(path)[2], 1)
    hits = len(HYPE_WORDS.findall(transcript)) + transcript.count('!')
    talk = 0.6 * min(1, hits / 5) + 0.4 * min(1, len(words) / dur / 3)
    bonus = 0.1 if __import__('re').search(r'insane|crazy|funny|lmao|lol|wtf|chase|crash|cops|police|rage|fail|exploded|wasted|no way|bro', title, __import__('re').I) else 0
    score = round(min(1, 0.40 * audio + 0.25 * motion + 0.35 * talk + bonus), 3)
    return score, {'audio': round(float(audio), 2), 'motion': round(float(motion), 2), 'talk': round(talk, 2), 'bonus': bonus}
