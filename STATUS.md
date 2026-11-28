# Status

## Week of 2026-11-23 (research)
The Monday 7:23 outlier scan had not run by 07:45 (latest data is the 2026-11-19 scan). That scan: 71 outliers across 20 channels (6 new, 41 dropped off); median winning compilation 17:23. New this week: all 6 from Sirloin (GTA 6 first-look/scene/walkthrough uploads, top 67.7x 'The Military Base in GTA 6 - First Look'). Dropped: older Among Us / Mafia 3 / God of War clips from Sanflix and AFGuidesHD, two Caylus videos, one Kai Cenat Live. THUMBNAILS.md rewritten: little or no text, one bright subject, no arrows/circles.

Daily episodes and weekly research notes. Newest at the bottom.


2026-11-19 episode review (07:45 Tucson): STOPPED - no episode/2026-11-19/plan.json. The scheduled `daily episode` workflow (cron 13:07 UTC) never ran today; no run appears in Actions as of 14:46 UTC (GitHub schedules also skipped most `fetch clips` slots overnight). Nothing approved or uploaded. Fix: run `daily episode` manually (workflow_dispatch) and re-run the review.
- 2026-11-18 episode #1 "GTA 6 Best Clips of the Day #1 (test)" https://youtu.be/L7cnJf-Ge0k (18 clips, 10:49, thumbnail FAILED (HTTP Error 403: Forbidden) - verify the channel phone number in YouTube Studio)
- 2026-11-20 episode #3 "xQc, CaseOh & Kai Cenat's Wildest GTA 6 Moments" https://youtu.be/tQmNKMMmtqQ (10 clips, 5:03, thumbnail thumb_B.jpg)
- 2026-11-21 episode #5 "Lacy Got Sent Flying in GTA 6" https://youtu.be/oFHQY_Jby6g (5 clips, 3:40, thumbnail thumb_A.jpg)
- 2026-11-22 episode #7 "Jynxzi Plowed Through a GTA 6 Police Roadblock" https://youtu.be/AaLCI9ao5cs (7 clips, 3:57, thumbnail thumb_A.jpg)
- 2026-11-23 episode #9 "IShowSpeed Found an Alien in GTA 6" https://youtu.be/VjRNakK0534 (3 clips, 1:12, thumbnail thumb_A.jpg)
- 2026-11-24 episode #11 "IShowSpeed Shot Down a GTA 6 Helicopter From a Boat" https://youtu.be/TMEUFoBRnrs (4 clips, 2:24, thumbnail thumb_B.jpg)
- 2026-11-25 episode #13 "Hasan Jumped Off a Roof in GTA 6... It Went Wrong" https://youtu.be/YU0rhHDlSt4 (6 clips, 3:01, thumbnail thumb_B.jpg)
- 2026-11-26 episode #15 "Fighter Jets Hunted TheBurntPeanut in GTA 6" https://youtu.be/Wa5acWPb3Ec (4 clips, 1:50, thumbnail thumb_A.jpg)
- 2026-11-27 episode #17 "Jynxzi Started a Rooftop Brawl in GTA 6" https://youtu.be/wBizv6Vz7M4 (2 clips, 1:20, thumbnail thumb_C.jpg)
2026-11-27 review: the 6:07 daily episode cron skipped; build requested by the review run at 14:47 UTC. Thin day: only 6 candidate clips, 4 dropped (2 ohnePixel PS5-menu dead air, Hasan story-mission dialogue, ohnePixel bathroom-following clip), so the episode is 2 clips / 1:20. Gauntlet NO (handle over facecam, 2 clips) -> fixed handle -> YES round 2.
2026-11-28 episode review (07:45 Tucson): STOPPED - no episode today. The 6:07 `daily episode` cron skipped again (no scheduled run in Actions); the review run requested a build at 14:47 UTC (episode/requests/2026-11-28-auto.json) and it failed: "only 4 usable clips today, not enough for an episode" (build.py needs 6). No plan.json, so no labels, gauntlet, approval or upload. Retrying will not help until more clips are fetched.
