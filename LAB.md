# Weekly lab procedure

Run once a week by a scheduled Claude task. Goal: ship ONE new option the learner can test, without breaking the pipeline.

1. Read `LEARNING.md` and `IDEAS.md`. Pick the top unchecked idea. If the learner shows a clear loser (an option with
   evidence >= 6 and lift below -0.4), you may instead retire that option from `learn.GENES` (keep at least 2 options per gene).
2. Build it behind a gene option so old behaviour stays available: add the option to `learn.GENES` and handle it in
   `clipper/render.py` (editing / layout) or `clipper/fetch.py` (picking) or `finish.py` (hook / caption).
3. Test it locally on a synthetic clip (ffmpeg testsrc2 + a sine burst) AND, if any clip is in `queue/` or `out/`,
   on a real one: render, extract frames at 1 s, 3 s and the middle, and look at them. The output must be 1080x1920,
   have audio, and the captions, hook and credit must be readable and not overlap the facecam.
4. Run `python3 -m py_compile clipper/*.py finish.py learn.py`.
5. Check the idea off in `IDEAS.md`, add a line to the changelog below, commit and push.
6. Report: what you built, how it is wired into the genes, and what the learner currently says is winning.

## Changelog
- 2026-11-18: learning loop v1 - edit genes (max_len, trim, cap_size, cap_pos, hook_style, hook_case), clip ranking by
  streamer / topic / length / hour, trend radar (game-wide Twitch clips + live streams + YouTube Shorts titles), auto-adds breakout English streamers (CLIP_LANGS).
- 2026-11-22: gene `punch` (off/on): 1.15x punch-in on the gameplay panel for 0.6 s at the loudest moment (render.punch_at / punch_chain).
  Facecam panel, captions, credit and hook are not zoomed. Tested on synthetic testsrc2 + sine burst and a real Silky cam clip.
