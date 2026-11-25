#!/usr/bin/env python3
"""chapters.py - YouTube chapter rules for episode descriptions (from Jake Schincariol's
youtube-agent-skill, MIT, see LICENSE). A chapter block that breaks any rule silently does
not render: the first entry must be 0:00, there must be 3+ entries, each at least 10 s long.

valid(chapters, total) takes [(start_seconds, text), ...] and returns a fixed list:
chapters shorter than 10 s are merged into the previous one ("A / B"), the first is forced
to 0:00, and if fewer than 3 remain it returns [] (better no chapters than a broken block)."""
MIN = 10.0


def mmss(t):
    t = int(t)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)


def valid(chapters, total):
    ch = [[float(s), txt] for s, txt in sorted(chapters)]
    if not ch:
        return []
    ch[0][0] = 0.0
    out = []
    for i, (s, txt) in enumerate(ch):
        end = ch[i + 1][0] if i + 1 < len(ch) else total
        if out and end - s < MIN:
            out[-1][1] += ' / ' + txt          # too short: fold into the previous chapter
        elif not out and end - s < MIN and i + 1 < len(ch):
            ch[i + 1][0] = 0.0                  # very first clip too short: the next one starts at 0:00
            ch[i + 1][1] = txt + ' / ' + ch[i + 1][1]
        else:
            out.append([s, txt])
    if len(out) >= 2 and total - out[-1][0] < MIN:
        last = out.pop()
        out[-1][1] += ' / ' + last[1]
    return [(s, t) for s, t in out] if len(out) >= 3 else []


def lines(chapters, total):
    return ['%s %s' % (mmss(s), t) for s, t in valid(chapters, total)]


if __name__ == '__main__':
    demo = [(0, 'A'), (11.4, 'B'), (31, 'C'), (36, 'short D'), (60, 'E'), (90, 'F')]
    print('\n'.join(lines(demo, 125)))
    print(lines([(0, 'A'), (5, 'B')], 20))
