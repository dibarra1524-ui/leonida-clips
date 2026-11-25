# Daily episode review run (scheduled Claude task, ~7:45 am Tucson)

The 6:07 am `daily episode` workflow has already built today's cut: `episode/<date>/plan.json`, `frames/NN.jpg`
(one per clip, label visible), `thumb_A/B/C.jpg`, `description.txt`, video in the `ep-<date>` GitHub release.
Your job: labels, title, thumbnail, then **the gauntlet rule** - nothing posts until a fresh agent says yes.

1. `git pull`. Today = Tucson date. If `approved.json` already exists, stop. If `episode/<date>/plan.json` is missing
   (GitHub's cron sometimes skips the 6:07 build), commit `episode/requests/<date>-auto.json` =
   `{"date": "<date>", "relabel": false}` and push; that starts the build. Poll `git pull` every 2 min (max 45 min)
   until plan.json appears. If it still has not appeared, say so in STATUS.md and stop.
2. **Labels.** Look at every frame and clip title. Write `episode/labels/<date>.json` = `{clip_id: "label"}`:
   one line, max 6 words, says what happens ("Kai crashes the police chopper"), no story spoilers, no slurs.
   Add `"_drop": [ids]` for clips that are cutscenes, endings, exploits/glitch tutorials, dead air, a bad crop,
   or ANY sexual content: strip clubs, lap dances, nude or topless NPCs, even in the background (titles like "Wtf"
   hide these, so judge the frame, not the title; YouTube age-restricts and stops recommending those videos).
   Also drop clips whose title or audio is mostly a slur being shouted.
   Set `"_first": id` to the clip that should open the episode: the most instantly gripping moment (chaos,
   a crash, a huge reaction), never a loading screen, a black frame or a slow start.
   The rebuild also remakes the 3 thumbnails without the dropped clips.
   Then commit `episode/requests/<date>-rN.json` = `{"date": "<date>", "relabel": true}` and push; that rebuilds
   the same clips with the new labels. Poll `git pull` every 2 min (max 40 min) until `plan.json` `built_at` changes.
3. **Title + thumbnail.** Read `research/THUMBNAILS.md`, `research/outliers.csv` (format=long) and `RULES.md`.
   Write 5 titles in the winning patterns (short, curiosity, a name if a big streamer is in it, under 60 chars).
   Re-letter any thumbnail if the text is weak: `python episode/thumbs.py redo <date> B "TEXT" [arrow|circle|none] [x y]`
   (2-3 big words, x/y = where the action is, 0-1).
   **Package check** (title + thumbnail together, from youtube-agent-skill): for each title run
   `python3 tools/yt/title.py --title "TITLE" --thumb "THUMB TEXT"` with the thumbnail text you plan to pair it with.
   Fix any title with `length` (over 60 chars), `duplicate` (the thumbnail repeats the title's words - the thumbnail
   must say what the title does not), `shouting` or `front-load`; keep the subject inside the first ~40 characters
   (the mobile cut). Give the gauntlet agent the 5 titles with their scores. A `no-number` note is only a hint.
   Chapters in description.txt are built and checked automatically (0:00 first, 3+, 10 s+ each); if the
   description has no chapter lines, that is because the clips could not meet YouTube's rules.
4. **Gauntlet.** Spawn a FRESH agent (Agent tool, general-purpose) that has not seen your notes. Give it only:
   the frame paths, the 3 thumbnail paths, the 5 titles, 4 outlier thumbnails from `research/thumbs/` with their titles,
   and the rules in `RULES.md`. Ask it to: pick the best title and thumbnail, then answer **yes or no** for the
   episode with every fault it found (bad crop, label typo/wrong, spoiler, gore in a thumbnail, misleading
   thumbnail, repeat clip, weak opener, text unreadable on a phone). It must not see your reasoning.
5. **No** -> fix what it named (relabel/drop + rebuild, or redo the thumbnail, or new titles) and run a NEW fresh
   agent. Max 3 rounds; still no -> do not post, log it in STATUS.md.
   **Yes** -> write `episode/<date>/approved.json`:
   `{"verdict": "yes", "title": "...", "thumbnail": "episode/<date>/thumb_X.jpg", "faults_fixed": [...], "rounds": N}`
6. Append every verdict (yes or no, with faults) as one line to `RULES.md` under `## Verdicts`. When the same fault
   shows up 3+ times, add a plain rule for it under `## Rules`.
7. Commit + push. The push of `approved.json` triggers the `upload episode` workflow, which posts to YouTube and
   writes the link to `STATUS.md`. After ~15 min `git pull` and confirm the link is there; if the workflow failed,
   say so in STATUS.md.

## Mondays, before step 1
The `outlier research` workflow ran at 7:23 am. Study `research/thumbs/*.jpg` against `research/outliers.csv` and
rewrite `research/THUMBNAILS.md`: what repeats (text length, colours, faces vs cars, arrows/circles, framing),
what the winning titles have in common, and a `## Words` list of 5-10 thumbnail phrases. Summarise
`research/CHANGES.md` (what changed this week) at the top of STATUS.md.
