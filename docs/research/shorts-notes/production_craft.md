# Production craft and technical specifications for vertical (9:16) animated educational Shorts (Manim-based), as of 2026

Research-access note for the report writer: this session's network blocked direct fetches of `support.google.com`, `arxiv.org` and most blogs. Official YouTube Help pages and arXiv papers are therefore cited via search-result summaries of those pages, not full-page reads. GitHub raw files (ManimGL source at tag v1.7.2, 3b1b/videos config, Code2Video and TheoremExplainAgent READMEs) were read directly. Each section flags which numbers are official, which are third-party estimates, and which are my own arithmetic.

## 1. Technical specs for YouTube Shorts in 2026 (resolution, aspect ratio, frame rate, length, bitrate/codec, loudness, upload details)

### Takeaway
Render a 1080x1920 (9:16) H.264 High Profile MP4 with AAC-LC 48 kHz audio, at up to 3 minutes, mixed to about -14 LUFS integrated with about -1 dBTP true peak. The length and classification rules are official; the loudness target and any "upload above 1080p for better quality" advice are third-party and unconfirmed for Shorts.

### Cited Findings
- Official classification rule: a video is a Short when it has "a square or vertical aspect ratio" and is "up to three minutes in length"; applies to uploads on or after October 15, 2024 (previous cap 60 seconds). Horizontal videos are never Shorts regardless of length. — [YouTube Help: Understand three-minute YouTube Shorts](https://support.google.com/youtube/answer/15424877?hl=en) (via search summary); corroborated by [reap.video](https://reap.video/blog/youtube-shorts-size)
- Official Content ID rule: a Short longer than one minute with an active Content ID claim is blocked globally, whatever the claim's policy; the creator is notified and the Short becomes viewable and monetizable again once the claim is resolved (remove claimed audio or dispute). One blog claims this is no longer automatic; that contradicts the Help text. — [YouTube Help](https://support.google.com/youtube/answer/15424877?hl=en); contradicted by [argil.ai](https://www.argil.ai/blog/how-long-can-youtube-shorts-be)
- Recommended resolution 1080x1920 at 9:16; third-party guides list 720x1280 as a floor and say Shorts playback is limited to 1080p. — [reap.video](https://reap.video/blog/youtube-shorts-size); [postfa.st](https://postfa.st/sizes/youtube/shorts)
- No official minimum length. — [async.com](https://async.com/blog/how-long-can-youtube-shorts-be/)
- Official encoding recommendations (general uploads, apply to Shorts too): MP4 container; H.264, High Profile, progressive scan, variable bitrate, 4:2:0 chroma subsampling; "no bitrate limit required"; AAC-LC audio, 48 kHz (or 96 kHz); recommended audio bitrate 384 kbps stereo; encode and upload at the source frame rate. — [YouTube Help: Recommended upload encoding settings](https://support.google.com/youtube/answer/1722171?hl=en) (via search summary)
- Recommended SDR video bitrates commonly quoted from that page: 1080p = 8 Mbps at 24/25/30 fps, 12 Mbps at 48/50/60 fps; 1440p = 24 Mbps at 60 fps; 4K = 53 to 68 Mbps at 60 fps. The 1080p row was confirmed only through third-party copies of the table. — [streamersize](https://streamersize.pages.dev/blog/best-video-format-for-youtube/); [soundstripe](https://www.soundstripe.com/blogs/youtube-video-formats)
- Accepted frame rates: 24, 25, 30, 48, 50, 60 fps, matching source. — [compresto.app](https://compresto.app/blog/youtube-shorts-size)
- YouTube re-encodes every upload to VP9 or AV1 and cuts bitrate in the process. Some editors claim uploading 1440p/4K earns a higher-bitrate transcode (one unsourced 2026 VMAF test: 1080p upload ~97.3, 1440p ~99.95, 4K ~99.92); others say the gain is imperceptible. None of this was measured for Shorts. — [clip.dor.gg](https://clip.dor.gg/en/blog/upload-game-video-without-quality-loss); [compresto.app](https://compresto.app/blog/best-video-format-for-youtube)
- Loudness: YouTube's reference is -14 LUFS integrated. It turns down content louder than -14 LUFS but does not turn up quieter content. — [Production Advice](https://productionadvice.co.uk/youtube-loudness/); [Youlean](https://youlean.co/?p=30813)
- Mix target advice: -14 LUFS integrated, -1 to -2 dB true peak on the master bus. — [Descript](https://www.descript.com/blog/article/how-to-set-the-perfect-audio-levels-for-video); a vendor post says the same target applies to Reels, TikTok and Shorts — [dev.to](https://dev.to/arthurvalle1_a2586dd2b4bc/mastering-for-reels-tiktok-and-youtube-shorts-the-lufs-14-standard-2ck5)
- ManimGL's default writer already emits `libx264` / `yuv420p`, which matches YouTube's H.264 4:2:0 recommendation. — [3b1b/manim default_config.yml](https://raw.githubusercontent.com/3b1b/manim/master/manimlib/default_config.yml)

### Inferences
- A Short quieter than -14 LUFS will simply play quieter than neighbours in the feed, because YouTube only attenuates. Normalize the final mix up to about -14 LUFS in ffmpeg (`loudnorm=I=-14:TP=-1:LRA=11`, two-pass) instead of trusting the platform.
- For flat-colour vector animation (thin lines, text, gradients on dark background), 60 fps at 12 Mbps+ is cheap to produce and helps motion smoothness; 30 fps at 8 Mbps is the minimum sensible export. Since "no bitrate limit" applies, exporting at CRF 14 to 18 or 15 to 20 Mbps costs nothing for a sub-3-minute file.
- Staying at or under 60 seconds keeps the widest music options (commercial tracks are effectively unusable above 1 minute because any Content ID claim blocks the Short). With claim-free music, the 60 to 180 second range is open.

### Gaps
- Could not read the official encoding page directly, so the bitrate table is second-hand. Verify at support.google.com/youtube/answer/1722171.
- No official YouTube statement found that Shorts playback is capped at 1080p, or that -14 LUFS normalization and the "Stable volume" feature apply to Shorts.
- No controlled test found of 1440x2560 versus 1080x1920 uploads for Shorts quality.

## 2. Safe zones: Shorts UI coverage on a 1080x1920 frame, and comparison with TikTok and Reels

### Takeaway
No platform publishes an official safe zone for organic Shorts; published numbers come from Google's ads template or from reverse-engineered overlays, and they disagree widely. The most conservative, most-cited Shorts figure is top 288 px, bottom 672 px, left 48 px, right 192 px (clear area 840x960); for cross-posting, a centred box of about 900x1400 is the common compromise.

### Cited Findings
- Most-cited Shorts set on 1080x1920: clear area 840x960 px beginning 288 px from the top and 48 px from the left; 672 px reserved at the bottom (channel name, Subscribe, title/description), 192 px on the right (like, comment, share, remix buttons). As percentages: top 15%, bottom 35%, left 4.4%, right 17.8%. — [revid.ai checker](https://www.revid.ai/youtube-shorts-safe-zone); same values at [socialync.io](https://www.socialync.io/tools/safe-zones/youtube-shorts)
- Those pixel values are stated to come from the 1080x1920 safe-zone image in Google's help article on vertical video ads, leaving about 39% of the frame clear. — [adkit.so](https://adkit.so/tools/safe-zones/youtube); official host page (numbers are in images only): [Google Ads Help, Demand Gen asset specs](https://support.google.com/google-ads/answer/13704860?hl=en)
- Looser Shorts estimate: central safe zone 984x1500, about 120 px blocked at top, 300 px at bottom, 96 px on the right. — [AdaptlyPost](https://adaptlypost.com/en/blog/social-media-safe-zones-2026-complete-guide)
- Another Shorts estimate: top 180 px, bottom 390 px, sides 60 px. — [flowshorts.app](https://flowshorts.app/blog/youtube-shorts-dimensions)
- Caveat stated by sources: pixel values were reverse-engineered from in-app overlays, are approximations, and the layout changes; Meta, Google and TikTok publish safe zones primarily for ads, not organic posts. — [reap.video](https://reap.video/blog/short-form-video-safe-zones)
- TikTok: central area about 900x1492; bottom margin 250 to 370 px. Instagram Reels: central area about 996x1400, top 210 px, bottom 310 px (other guides: bottom 320 to 420 px). Reels also has a second crop to plan for (profile-grid/feed preview). — [AdaptlyPost](https://adaptlypost.com/en/blog/social-media-safe-zones-2026-complete-guide); [admakeai](https://www.admakeai.com/blog/vertical-video-dimensions-2026)
- Meta's Reels ads guidance in percentages: 14% top, 35% bottom, 6% each side. — [trymypost](https://www.trymypost.com/blog/instagram-reels-safe-zones-2026)
- Universal cross-platform box: 900x1400 centred in 1080x1920 — [AdaptlyPost](https://adaptlypost.com/blog/social-media-safe-zones-2026-complete-guide); about 880x1420 — [argil.ai](https://argil.ai/blog/tiktok-aspect-ratio-cec4c)
- YouTube's safe zone sits left of centre because the right margin is larger than the left. — [revid.ai](https://www.revid.ai/youtube-shorts-safe-zone)

Comparison table (1080x1920, px blocked from each edge; all third-party):

| Platform / source | Top | Bottom | Left | Right | Clear area |
|---|---|---|---|---|---|
| Shorts, Google ads template (revid, socialync, adkit) | 288 | 672 | 48 | 192 | 840x960 |
| Shorts, AdaptlyPost (organic estimate) | ~120 | ~300 | n/a | ~96 | 984x1500 |
| Shorts, flowshorts | 180 | 390 | 60 | 60 | 960x1350 |
| TikTok, AdaptlyPost / admakeai | n/a | 250 to 370 | n/a | n/a | 900x1492 |
| Reels, AdaptlyPost | 210 | 310 (others 320 to 420) | n/a | n/a | 996x1400 |
| Reels ads, Meta percentages | 14% (269) | 35% (672) | 6% (65) | 6% (65) | ~950x979 |
| Universal | n/a | n/a | n/a | n/a | 900x1400 or 880x1420 |

### Inferences
- Two-tier layout rule: (a) "critical" tier, the 840x960 ads box, for anything that must never be covered (caption text, equation result, punchline); (b) "content" tier, roughly x 60 to 960 px and y 180 to 1500 px, for the animation itself, which tolerates partial overlap by semi-transparent UI. The bottom 300 to 400 px and the right 100 to 190 px column should hold only background.
- In ManimGL units (my arithmetic; see section 6 for frame sizes). With `frame_height: 8` at 1080x1920, 1 unit = 240 px and the frame is 4.5 wide: the 840x960 critical box spans x from -2.05 to +1.45 and y from -1.2 to +2.8, centre (-0.3, +0.8). With `frame_height: 16` (frame 9 wide, 1 unit = 120 px) it spans x from -4.1 to +2.9 and y from -2.4 to +5.6, centre (-0.6, +1.6). So the visual centre of a Short is above and slightly left of the origin, not at ORIGIN.
- The 672 px bottom figure is the worst case for ads (CTA button plus long description). Organic Shorts with a one-line title cover less, which explains the 300 to 390 px estimates.

### Gaps
- No first-party pixel map for organic Shorts exists. Google's ad safe-zone PNG could not be downloaded here to measure directly.
- The top search bar/back arrow height on Shorts is not separately specified by any source; it is folded into the "top" margin (120 to 288 px).
- All numbers should be verified by uploading an unlisted test Short with a pixel grid and viewing it in the current iOS and Android apps.

## 3. Typography and captions: legible size, words on screen, caption styles, placement, contrast, and evidence on muted viewing and retention

### Takeaway
Use bold sans-serif text of about 55 to 75 px (48 px floor) on a 1080x1920 canvas, with burned-in captions placed inside the safe box. Evidence that captions help is solid in general (survey data) but weak and vendor-sourced for the specific claim that word-by-word animated captions raise retention.

### Cited Findings
- Size: 48 px is given as the minimum for any caption that carries meaning; 60 to 75 px is the optimal range for educational/talking-head content at 1080x1920. — [blitzcutai](https://blitzcutai.com/blog/best-caption-size-youtube-shorts-2026)
- Lower figures also circulate: 36 px minimum, 40 to 60 px typical; as a share of frame height, 7 to 9% (about 60 to 80 px by that source's reckoning), and "below 5% of frame height is too small". — [fluxnote](https://fluxnote.io/guides/faceless-shorts-text-overlay-tips-2026); [OpenClip caption styling guide](https://openclip.app/guides/caption-styling-guide)
- Weight matters more than size: a 55 px bold (700) caption reads better than a 70 px thin (300) one. Desktop previews mislead; test on a real phone. — [blitzcutai](https://blitzcutai.com/blog/best-caption-size-tiktok-2026)
- Muted viewing share is inconsistently reported: 70 to 85% of short-form social video, a flat "85%", or about 60% of TikTok/Reels viewers. All are vendor-blog figures without primary data for Shorts. — [clipspeed.ai](https://clipspeed.ai/blog/ai-captions-increase-views.html); [videocaptions.ai](https://www.videocaptions.ai/blog/how-video-captions-increase-engagement)
- Best-supported study: a 2019 Verizon Media/Publicis Media survey of about 5,600 US adults found 80% said they were more likely to finish a video when captions were available. It covers captions generally, not word-by-word styling, and is self-reported. — [clipspeed.ai](https://clipspeed.ai/blog/ai-captions-increase-views.html)
- Word-by-word/animated versus static captions, vendor claims only: +18 to 23% average watch time; +10 to 20% watch time in creator tests; +34% watch time for "word-by-word emphasis with colour" versus plain in a vendor's 80-video test; 15 to 25% completion uplift (unsourced). — [blitzcutai](https://blitzcutai.com/blog/animated-captions-vs-static-captions); [fluxnote](https://fluxnote.io/blog/ai-captions-that-improve-watch-time); [dev.to](https://dev.to/clamper_ai/how-ai-caption-styles-affect-watch-time-and-engagement-409i)
- The only academic work surfaced on word highlighting is a 2023 arXiv paper showing time-synchronised word highlighting improved comprehension in language learning, not short-form retention. — [clipspeed.ai](https://clipspeed.ai/blog/ai-captions-increase-views.html)
- Placement: keep key text in the central safe area; avoid captions at the bottom edge and avoid key motion at the top edge where UI overlaps. — [studioliddell](https://studioliddell.com/news/reels-in-motion-a-production-guide-for-brands-and-studios); [vidio.ai](https://www.vidio.ai/blog/article/what-is-vertical-pacing-and-how-should-editors-adapt-cuts-and-motion-for-916-videos)
- ManimGL text scale: `font_size_for_unit_height: 144` for both Text and Tex, meaning font size 144 makes the glyph "0" one Manim unit tall. — [3b1b/manim default_config.yml](https://raw.githubusercontent.com/3b1b/manim/master/manimlib/default_config.yml)

### Inferences
- ManimGL font-size conversion at 1080x1920 (my arithmetic): digit height in px = font_size / 144 x (1920 / frame_height). With `frame_height: 8` (240 px per unit), font_size 36 gives a 60 px-tall digit and 48 gives 80 px; with `frame_height: 16` (120 px per unit), font_size 72 gives 60 px and 96 gives 80 px. Note that caption-tool "px" sizes are CSS font sizes (em height), whose digits are roughly 70% as tall, so a "64 px caption" corresponds to about a 45 px-tall digit. Practical floors for ManimGL: body labels at least 40 to 45 px digit height, captions 45 to 60 px, hero numbers/equations 80 px+.
- Words on screen: no source gave a number. Fitting bold 60 to 70 px text into an 840 px-wide safe box allows roughly 14 to 18 characters per line, which is why the dominant creator styles are 1 to 3 words per chunk or at most two short lines. This is a geometric inference, not a cited finding.
- For an animation-first maths Short, the caption competes with the visual for the same viewer attention. A defensible design is short caption chunks (2 to 4 words) in a fixed band just below the visual, at y of about 1150 to 1250 px, inside the conservative box's lower edge (1248 px), with the active word highlighted in one accent colour.
- Contrast: white or near-white bold text with a dark stroke or semi-opaque dark plate on a dark background meets the "heavy weight, high contrast" advice; thin maths fonts (CMU Serif, default Tex strokes) need larger sizes than sans-serif captions for equal legibility.

### Gaps
- No independent, peer-reviewed study isolates word-by-word captions and measures Shorts retention.
- No reliable primary figure for the share of YouTube Shorts watched muted (Shorts autoplays with sound in the feed, unlike Facebook's historical muted autoplay, so the "85%" figure is likely inflated for Shorts; I found no source to confirm either way).
- No cited words-per-chunk or reading-speed figure for captions; the cursa.app page that addresses line length could not be read.

## 4. Motion design for small vertical screens: density, camera moves, stacking, colour, and adapting wide maths visuals

### Takeaway
Editor guidance is consistent but qualitative: design natively for 9:16 instead of cropping, show one idea at a time, make everything large, keep faster pacing than landscape, and keep motion away from UI-covered edges. I found no source that specifically addresses laying out graphs, number lines or equations in a tall frame, so that part is inference.

### Cited Findings
- Build natively for vertical; cropping landscape to 9:16 discards 56 to 70% of the image area. — [vidu.com](https://www.vidu.com/en/blog/mobile-video-ai)
- One idea, one feature, one moment or one outcome per vertical clip; cut around it. — [dreamina/capcut](https://dreamina.capcut.com/ai-video/resize-product-video-ad-placements)
- Pacing: landscape content is typically slow and vertical content is fast; cuts and motion must be re-timed, not just reframed. — [vidio.ai](https://www.vidio.ai/blog/article/what-is-vertical-pacing-and-how-should-editors-adapt-cuts-and-motion-for-916-videos)
- Use a toggleable safe-zone overlay during production; keep key text in the central safe area; text and visuals must be large and readable on mobile. — [studioliddell](https://studioliddell.com/news/reels-in-motion-a-production-guide-for-brands-and-studios); [clueso](https://www.clueso.io/glossary/vertical-video)
- Educational shorts: fast, short delivery and one learning objective per segment. — [CapCut](https://www.capcut.com/create/fast-cut-video-audio-editing-tone-and-pacing)
- 3Blue1Brown's own render config is landscape 3840x2160 at 30 fps, black background `#000000`, font "CMU Serif", saturation 1.5; it contains no vertical or Shorts settings. — [3b1b/videos custom_config.yml](https://raw.githubusercontent.com/3b1b/videos/master/custom_config.yml)
- In LLM-generated Manim videos, the dominant visual defects are misaligned text, overlapping shapes, uneven object sizes, viewport overflow and cut-off content (details in section 7). — [TheoremExplainAgent](https://arxiv.org/html/2502.19400v2); [OmniManim](https://arxiv.org/pdf/2605.15585)

### Inferences
- Frame budget: a ManimGL vertical frame at the default `frame_height: 8` is only 4.5 units wide versus 14.2 in landscape (FRAME_WIDTH = FRAME_HEIGHT x aspect ratio). Any landscape scene wider than about 3.5 units (the safe width) overflows. Wide objects must be redesigned, not scaled: scaling a 12-unit number line to 3.5 units shrinks its labels to roughly 30% size, below the legibility floor.
- Adapting wide maths visuals (inferred patterns):
  - Number lines and axes: show a short range (5 to 7 ticks), or rotate to a vertical number line, or pan the camera along the line with `self.frame` rather than showing it all.
  - Graphs: use square or portrait axes (for example 3.4 units wide by 3.4 to 4.5 tall), fewer tick labels, thicker strokes; put the axis label inside the plot area.
  - Equations: break at relation signs and stack lines vertically (`arrange(DOWN)`); show one transformation step at a time using `TransformMatchingTex`-style replacement rather than a growing derivation; keep each line under about 3.5 units wide.
  - Side-by-side comparisons become top/bottom comparisons.
- Stacking layout: a three-band layout matches the safe-zone geometry: a hook/title band at about y 290 to 450 px, the main visual at about y 450 to 1100 px, and caption/equation band at about y 1100 to 1250 px, with nothing important below 1250 px (conservative) or 1500 px (lenient).
- Camera: on a small screen, zoom-ins on the active element substitute for the detail a wide frame would show; in ManimGL this is `self.frame.animate.reorient/scale/move_to`. Keep moves slow enough to read text during the move, and restrict to one move per beat.
- Colour and stroke: with the UI overlaying white icons and text, a dark background (3b1b uses pure black; ManimGL default is `#333333`) with 3 to 4 saturated accent colours keeps contrast; stroke widths should be increased relative to landscape because the same Manim-unit stroke occupies fewer physical millimetres on a phone.

### Gaps
- No authoritative source on vertical layouts for mathematical graphics; no documented account of how 3Blue1Brown produces its vertical Shorts (they appear to be re-edited from long-form videos, but I found no source confirming the method, so treat as unknown).
- No quantitative guidance (elements per frame, seconds per beat) from a reliable editor source; no eye-tracking or retention data for animated explainer Shorts.

## 5. Sound design: music, sound effects, licensing, levels, and whether top educational Shorts use music

### Takeaway
Voice first, music 18 to 24 dB below narration (up to 30 dB for dense tutorials), sparse SFX matched to motion and synced to the visual hit frame. For monetization safety the YouTube Audio Library and a channel-safelisted subscription (Epidemic Sound) are the best-supported options, and the over-one-minute Content ID block makes claim-free music mandatory for longer Shorts.

### Cited Findings
- Music under narration: about 18 to 24 dB quieter than the voice; 22 to 30 dB lower for dense tutorials. One guide computes music loudness as narration loudness minus a chosen separation (about 20 LU in its example). — [overseeros](https://www.overseeros.com/blog/background-music-volume-under-voiceover)
- Absolute-level variant: music at -18 to -25 dB while someone speaks, -12 to -14 dB when there is no speech. This is an absolute meter level, not a relative one; the two conventions should not be mixed. — [vidpros](https://vidpros.com/fix-background-music-too-loud-video/)
- Master to -14 LUFS integrated with -1 to -2 dB true peak. — [Descript](https://www.descript.com/blog/article/how-to-set-the-perfect-audio-levels-for-video)
- Restraint: a whoosh on every swipe, zoom and caption pop makes a short noisy; stacked effects that sound premium soloed turn to mush on a phone speaker. — [fish.audio](https://fish.audio/blog/using-sound-effects-for-short-form-content)
- Reducing sound design to "transition = whoosh, text = pop" is called a common mistake. — [overseeros sound design](https://www.overseeros.com/blog/youtube-sound-design)
- Match sound to movement (fast sideways swipe = short whoosh, zoom-in reveal = low pop or hit); do not reuse one identical whoosh on every cut. — [fish.audio](https://fish.audio/blog/using-sound-effects-for-short-form-content)
- Sync: treat music as the beat grid and SFX as punctuation; nudge the animation so the visual "hit frame" and the audio transient coincide, by a few frames either way. — [cursa.app](https://cursa.app/en/page/audio-and-final-rhythm-using-sound-to-support-motion)
- Workflow: set voice and music levels first, then add SFX on separate "hard" (impacts, clicks) and "soft" (whooshes, ambience) tracks. — [jestr.gg](https://www.jestr.gg/blog/how-to-be-a-better-editor)
- Avoid dense cinematic beds that hide claims and names. — [nemovideo](https://www.nemovideo.com/blog/ai-sound-effect-generator-short-videos)
- Licensing: YouTube Audio Library music does not receive Content ID claims. — [YouTube Help: Music eligibility for Shorts](https://support.google.com/youtube/answer/13486873?hl=en) (via search summary); [vidIQ](https://vidiq.com/ru/blog/post/how-to-best-free-music-youtube-royalty-free)
- Epidemic Sound: channels connected to a subscription are safelisted against claims (company's own framing; some creators report delays). Artlist: no automated Content ID safelist per one source, "usually fine but verify"; sources are inconsistent. Subscriptions limit the number of covered channels; tracks published while subscribed stay covered after cancelling, per one source. — [foximusic](https://www.foximusic.com/blog/music-licensing-companies-for-youtube/); [uppbeat](https://uppbeat.io/blog/royalty-free-and-copyright-free-music/6-best-music-catalogs-for-monetized-youtube-channels-in-2026); [fluxnote](https://fluxnote.io/guides/epidemic-sound-vs-artlist-2026)
- In May 2026 YouTube was reported to be letting creators swap claimed Content ID tracks for royalty-free AI substitutes. — [Complete Music Update](https://completemusicupdate.com/youtube-to-let-video-creators-swap-out-content-id-tracks-for-royalty-free-ai-substitutes)
- Shorts over one minute with any active Content ID claim are blocked globally. — [YouTube Help](https://support.google.com/youtube/answer/15424877?hl=en)

### Inferences
- For an automated pipeline, the lowest-risk stack is: TTS narration as the anchor, an instrumental bed from the YouTube Audio Library or CC0 sources ducked 20 to 24 dB under the voice (sidechain or static), and a small fixed SFX palette (one soft whoosh for camera moves, one tick/pop for object appearance, one low hit for the reveal) at roughly 6 to 12 dB under the voice, then loudnorm to -14 LUFS / -1 dBTP.
- Because Manim animation timings are known in code, SFX can be placed exactly on the frame where an animation ends (the "hit frame"), which is the sync advice above implemented deterministically. Limit to roughly one SFX per beat.
- Phone speakers reproduce little below about 200 Hz, so low rumbles and sub-bass hits carry poorly; mid-frequency clicks and pops survive better. (General audio knowledge, not from a cited source.)

### Gaps
- No data found on whether top educational Shorts channels use music, or its retention effect; no source documents 3Blue1Brown's Shorts audio approach.
- No YouTube-published music-to-voice ratio. No source confirms that vendor safelists and Audio Library immunity behave identically for Shorts as for long-form.
- CC0 sources (for example Freesound CC0, Pixabay) were not researched; licence terms of each should be checked before use, and CC0 tracks can still be fraudulently claimed in Content ID.

## 6. Manim specifics: rendering vertical video with ManimGL (3b1b, v1.7.2) versus Manim Community

### Takeaway
In ManimGL 1.7.2, vertical output only needs a resolution change: `FRAME_WIDTH` is derived as `frame_height x (pixel_width / pixel_height)`, so 1080x1920 with the default `frame_height: 8` gives a 4.5 x 8 unit frame. In Manim Community you set `pixel_width`, `pixel_height` and one of `frame_width`/`frame_height` and must avoid the `-q` quality flags. I found no public ManimGL Shorts example or tutorial.

### Cited Findings
ManimGL (3b1b/manim), verified against source at tag v1.7.2:
- `constants.py` derives the frame from the configured resolution: `DEFAULT_RESOLUTION = manim_config.camera.resolution`; `ASPECT_RATIO = DEFAULT_PIXEL_WIDTH / DEFAULT_PIXEL_HEIGHT`; `FRAME_HEIGHT = manim_config.sizes.frame_height`; `FRAME_WIDTH = FRAME_HEIGHT * ASPECT_RATIO`; `FRAME_X_RADIUS = FRAME_WIDTH / 2`; `FRAME_Y_RADIUS = FRAME_HEIGHT / 2`. — [manimlib/constants.py @ v1.7.2](https://raw.githubusercontent.com/3b1b/manim/v1.7.2/manimlib/constants.py)
- Resolution precedence in `config.py`: `-r/--resolution "WxH"` (parsed by `args.resolution.split("x")`), then `-l`, `-m`, `--hd`, `--uhd` (from `resolution_options`), else `camera.resolution` from YAML: `camera_config.resolution = arg_resolution or literal_eval(camera_config.resolution)`. `--fps` overrides `camera.fps`. — [manimlib/config.py @ v1.7.2](https://raw.githubusercontent.com/3b1b/manim/v1.7.2/manimlib/config.py)
- Defaults: `camera.resolution: (1920, 1080)`, `fps: 30`, `background_color: "#333333"`; `sizes.frame_height: 8.0`; `default_mobject_to_edge_buff: 0.5`; `resolution_options` low (854, 480), med (1280, 720), high (1920, 1080), 4k (3840, 2160); text font "Consolas"; `font_size_for_unit_height: 144` for Text and Tex; file writer `libx264`, `yuv420p`. — [manimlib/default_config.yml](https://raw.githubusercontent.com/3b1b/manim/master/manimlib/default_config.yml) (read from master; check against the v1.7.2 tag)
- A `custom_config.yml` in the project directory overrides the defaults, and `--config_file` selects a specific file. — [ManimGL docs: custom_config](https://code.amethyst.name/public-mirrors/3b1b-manim/src/branch/master/docs/source/documentation/custom_config.rst)
- Grant Sanderson's own production config is landscape 4K at 30 fps with no vertical settings. — [3b1b/videos custom_config.yml](https://raw.githubusercontent.com/3b1b/videos/master/custom_config.yml)

Manim Community:
- Set `pixel_width` and `pixel_height`, then set only one of `frame_width` or `frame_height` (the other is computed); do not use `-q/--quality`, because quality presets reset to the standard landscape aspect ratio. Example given: a portrait `manim.cfg` `[CLI]` section with 450x800 pixels and `frame_width = 8`. — [Stack Overflow answer (mirror)](https://kiwix.lema.org/stackoverflow.com_en_all_2023-11/questions/68529610/how-to-set-the-size-of-pic-video-rendered-by-manim)
- `config` is the single source of truth, with `pixel_width`, `pixel_height`, `frame_width`, `frame_height`, `frame_rate` attributes. — [Manim Community configuration guide](https://docs.manim.community/en/stable/guides/configuration.html); [ManimConfig reference](https://docs.manim.community/en/stable/reference/manim._config.utils.ManimConfig.html)
- In Manim Community, frame width is independent of pixel size; changing it zooms the scene instead of changing output dimensions. — [FlyingFrames, Resolution and Camera](https://flyingframes.readthedocs.io/en/latest/ch5.html)

### Inferences
Recommended ManimGL 1.7.2 vertical setup (derived from the source lines above; not tested here because ManimGL is not installed in this sandbox and the `manim/` submodule directory is empty):

```yaml
# custom_config.yml for Shorts renders (keep separate from the landscape one; select with --config_file)
camera:
  resolution: (1080, 1920)
  fps: 60          # or 30
  background_color: "#000000"
sizes:
  frame_height: 8.0   # gives a 4.5 x 8.0 unit frame; use 16.0 for a 9 x 16 unit frame
```

or per render: `manimgl scene.py SceneName -w -r 1080x1920 --fps 60`.

- Choice of `frame_height`: keeping 8.0 preserves the vertical coordinates of existing scenes (y from -4 to 4) but leaves only 4.5 units of width, and every object renders 1.78x larger in pixels than in a 1080p landscape render (240 px per unit versus 135). Setting 16.0 gives a 9 x 16 unit frame at 120 px per unit, so objects are about 0.89x their landscape pixel size and existing font sizes remain roughly valid; that is the smoother migration for a codebase and LLM prompts tuned to landscape sizes, at the cost of y now ranging from -8 to 8.
- Pitfalls to expect:
  1. `FRAME_WIDTH`, `FRAME_X_RADIUS` and the related constants are computed at import time from the config, so the vertical config must be in place before `manimlib` is imported; changing resolution inside a scene will not update them.
  2. Any hard-coded landscape coordinates (for example `x_range` spanning -7 to 7, `to_edge(LEFT)` assumptions, `RIGHT * 5`) and LLM-prompt rules that say "frame is 14.2 x 8" become wrong; `to_edge`/`to_corner` still work because they use the derived constants.
  3. The `-l/-m/--hd/--uhd` flags override `camera.resolution` with landscape presets, so a low-quality preview flag silently returns the render to 16:9. Use `-r 540x960` for previews, or redefine `resolution_options` in the vertical config.
  4. `Axes`/`NumberPlane` defaults size themselves from the frame constants, so a default plane becomes tall and narrow with unequal visible ranges; set `width`/`height` and ranges explicitly.
  5. The interactive preview window keeps its own size setting (`window.size`), so the preview aspect may not match unless set.
  6. Downstream steps that normalize to landscape will letterbox or stretch. In this repository, `manimgen/CLAUDE.md` documents that the assembler normalizes output to `1920x1080@60fps` from `rendering.resolution` / `rendering.fps` in `manimgen/config.yaml` (currently `resolution: "1920x1080"`, `fps: 60`), so a vertical mode needs that value, the ffmpeg scale/pad filters and xfade stage switched to 1080x1920 together with the ManimGL render flag.
  7. A model-generated reading of `config.py` suggests `--fps` is stored without an `int()` cast; if so, prefer setting `fps` in the YAML. Unverified, check the source.
- The safe-zone box in Manim units (section 2) should be exposed to scene code as constants (for example `SAFE_LEFT`, `SAFE_RIGHT`, `SAFE_TOP`, `SAFE_BOTTOM`, `SAFE_CENTER`) so that layout uses the safe box instead of frame edges; `to_edge` alone places content under the Shorts UI.

### Gaps
- No public example found of ManimGL (3b1b) being used for vertical Shorts: no GitHub issue, tutorial or showcase surfaced, and no documentation of how 3Blue1Brown's own Shorts are produced.
- Could not find or verify how "Mathematical Visual Proofs" or other Manim channels configure vertical renders; searches returned only generic Shorts-spec pages. Reddit r/manim and the Manim Discord were not reachable.
- manim-voiceover and Generative Manim were not found to have any vertical/Shorts-specific documentation in this research.
- The recommended config above is derived from source reading, not a test render; verify with a 2-second scene before adopting.

## 7. Automated pipelines: existing topic-to-explainer systems, output quality, and failure modes

### Takeaway
Research systems (TheoremExplainAgent, Code2Video, and follow-ups such as PhysicsSolutionAgent and OmniManim) all use planner plus coder agents emitting Manim code, increasingly with a vision-model critic. They reach high render-success rates but share one persistent weakness: spatial layout (overlap, misalignment, overflow) in videos whose code runs without error. All target landscape long-form video; none addresses vertical Shorts.

### Cited Findings
- TheoremExplainAgent (Ku et al., ACL 2025): a planner agent writes story plans and narration, a coding agent writes Manim scripts; optional RAG over Manim docs and a VLM that fixes code from rendered frames; narration via Kokoro TTS through a manim-voiceover-style service. Benchmark TheoremExplainBench has 240 theorems across STEM subjects. — [TheoremExplainAgent README](https://raw.githubusercontent.com/TIGER-AI-Lab/TheoremExplainAgent/main/README.md); [ACL Anthology](https://aclanthology.org/2025.acl-long.332)
- Reported results: the o3-mini agent reached a 93.8% success rate and 0.77 overall score; by metric, element layout was its weakest at 0.61 versus 0.89 logical flow and 0.88 visual consistency. — [project page](https://tiger-ai-lab.github.io/TheoremExplainAgent); [arXiv 2502.19400](https://arxiv.org/html/2502.19400v2)
- Reported failure modes: "most of the videos produced exhibit minor issues with visual element layout": misaligned text, overlapping shapes, uneven object sizes, most frequent on medium and hard theorems. The README FAQ lists LaTeX errors and API/config failures as common run failures. — [arXiv 2502.19400](https://arxiv.org/html/2502.19400v2); [README](https://raw.githubusercontent.com/TIGER-AI-Lab/TheoremExplainAgent/main/README.md)
- Code2Video (Show Lab, arXiv 2510.01174; NeurIPS 2025 DL4C workshop; code released 2025-10-02): three agents. Planner expands a knowledge point into a storyboard, Coder writes debuggable Manim code, Critic (a VLM) refines layout "with anchors" and checks aesthetics. Metrics: TeachQuiz (knowledge transfer: a VLM "unlearns" a concept and relearns it from the video), AES (aesthetic and structural quality), and efficiency (tokens, time). — [Code2Video README](https://raw.githubusercontent.com/showlab/Code2Video/main/README.md); [arXiv 2510.01174](https://arxiv.org/pdf/2510.01174)
- Code2Video benchmark MMMC: 117 curated 3Blue1Brown-inspired topics per the README; a review summary describes 456 professionally produced Manim tutorial videos across 13 subjects (different counts of different things; verify in the paper). Baselines included diffusion video models (OpenSora-v2, Wan2.2, Veo3), which scored near zero on logic flow and element layout, and direct code generation by GPT-5, GPT-4.1 and Claude Opus 4.1. Summaries report about 40% TeachQuiz improvement over direct code generation; human-made videos remain the reference. — [README](https://raw.githubusercontent.com/showlab/Code2Video/main/README.md); [Moonlight review](https://www.themoonlight.io/review/code2video-a-code-centric-paradigm-for-educational-video-generation)
- Code2Video's README recommends Claude-4-Opus for Manim code quality and Gemini 2.5 Pro as the VLM critic for layout. — [README](https://raw.githubusercontent.com/showlab/Code2Video/main/README.md)
- PhysicsSolutionAgent (arXiv 2601.13453): reports minor and major issues in visual layout and in how the AI evaluates images during feedback; names "Manim code hallucinations" and "LaTeX rendering errors" as primary failure categories; uses a VLM to screen rendered frames for overlap, poor text readability and cut-off content. — [arXiv 2601.13453](https://arxiv.org/pdf/2601.13453)
- OmniManim (arXiv 2605.15585): argues executable code still yields viewport overflow and element overlap, calls spatial layout failure "a fundamental bottleneck in LLM-based animation", and adds a Vision Agent that plans layout before code generation. — [arXiv 2605.15585](https://arxiv.org/pdf/2605.15585)

### Inferences
- Layout failure rates will be worse in 9:16 than in the landscape results above: the frame is about one-third as wide in Manim units, LLMs' Manim priors (training data, 3b1b code) are almost entirely landscape, and the safe box is smaller than the frame. A vertical pipeline should therefore (a) pre-plan layout into fixed bands or anchor slots before code generation (the Code2Video anchor and OmniManim pre-layout ideas), (b) run a deterministic bounding-box check against the safe box, not just the frame, and (c) keep a VLM frame review as the final gate. The existing manimgen pipeline already has a vision `layout_check` stage per its config, which is the same class of mitigation these papers converge on.
- Published metrics measure render success and rubric scores, not viewer retention, so "0.77 overall" says nothing about performance as a Short; hook strength, pacing and audio are outside every benchmark found.
- Reported strongest-model choices (Claude Opus for Manim code, a Gemini model as visual critic) date from 2025 and target Manim Community code; ManimGL has far less training data and a different API, so hallucinated Community-only APIs are an added failure class for a ManimGL pipeline.

### Gaps
- Could not read the arXiv papers directly (host blocked); numbers for TheoremExplainAgent come from the project page via search, and Code2Video's numeric results and limitations section were not retrieved.
- Generative Manim, manim-voiceover and commercial tools (for example topic-to-video SaaS products) were not successfully researched: no reliable quality reports or reviewer critiques surfaced.
- No system found that generates vertical short-form explainers programmatically with published quality evaluation; no human-audience (retention or learning outcome) evaluation of auto-generated Manim Shorts.
