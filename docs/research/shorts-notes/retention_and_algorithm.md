# YouTube Shorts: retention and distribution (2025-2026), for educational/explainer content

Method note for the report writer: this session's network blocked every direct page fetch (YouTube Help, Buffer, vidIQ, OpusClip, TechCrunch, Social Media Today, threadreaderapp, outlierkit, overseeros all failed to resolve). Everything below comes from web-search result summaries and snippets, not from reading the pages in full. Quotes are as rendered in those snippets and were not checked against the primary page. Each finding is labelled **[OFFICIAL]** (YouTube statement, usually via press coverage), **[DATA]** (study or dataset), or **[OPINION]** (vendor/creator blog, no verifiable method). Most [OPINION] sources are tool vendors with a commercial interest.

## 1. How does the Shorts algorithm measure success?

### Takeaway
YouTube officially exposes "viewed vs swiped away", engaged views and average percentage viewed, but publishes no weights, thresholds or test-batch sizes; every numeric cutoff in circulation is third-party. The best available benchmark is Paddy Galloway's 2023 study: under 60% viewed rarely performs, the best Shorts sit at 70 to 90% viewed.

### Cited Findings
- [OFFICIAL] YouTube Help defines "Shown in feed" as the number of times a Short appeared in the Shorts feed and "How many chose to view" as the percentage of times viewers viewed the Short versus swiped away; it is found in Studio under Analytics > Content > Shorts. — [outlierkit, citing YouTube Help](https://outlierkit.com/resources/youtube-shorts-algorithm/)
- [OFFICIAL, by absence] YouTube has not said the viewed-vs-swiped number is itself the ranking signal, and publishes no ranking weights, test-batch sizes or viewed-percentage thresholds. Claims of a fixed cutoff (for example "40% swipe-away is fatal") are not official. — [outlierkit](https://outlierkit.com/resources/youtube-shorts-algorithm/)
- [OFFICIAL] From 31 March 2025 a Shorts view is counted every time the Short starts to play or replays, with no minimum watch time (aligning with TikTok and Reels). The previous, stricter count survives as "engaged views" in YouTube Analytics Advanced Mode. Earnings and YPP eligibility still use engaged views. YouTube advises using engaged views when comparing pre- and post-31-March content. — [TechCrunch](https://techcrunch.com/2025/03/26/youtube-is-changing-how-youtube-shorts-views-are-counted); [Tubefilter](https://www.tubefilter.com/2025/03/26/youtube-shorts-views-counting-stats/); [MediaPost](https://www.mediapost.com/publications/article/404562/youtube-changes-how-it-counts-shorts-views.html)
- [OFFICIAL, DATED 2023] Todd Sherman (Shorts product lead), interviewed by Rene Ritchie on Creator Insider in August 2023: the Shorts system is different from long-form recommendation because the format and consumption behaviour differ; a view was then meant to reflect intent, with a "meaningful threshold" YouTube would not disclose; not every flip in the feed counted as a view. The view-threshold part is superseded by the March 2025 change. — [Search Engine Journal](https://www.searchenginejournal.com/youtube-explains-how-shorts-algorithm-works/494953/); [Social Media Today](https://www.socialmediatoday.com/news/youtube-advice-for-shorts-creators-hashtags-algorithm/691835/); [MediaPost](https://www.mediapost.com/publications/article/388588/youtube-shorts-product-lead-offers-insights-for-cr.html)
- [DATA, DATED April 2023] Paddy Galloway and analyst Chris Gileta: 3.3 billion Shorts views, 5,400 Shorts, 33 channels across niches. "Shorts with under 60% VVSA rarely performed well. The best performing shorts were typically between 70 and 90%" viewed. Galloway labels these "initial results (subject to more testing)". — [Galloway on X](https://x.com/PaddyG96/status/1646898368495382528); [thread](https://x.com/PaddyG96/status/1646898356419981315?lang=en)
- [DATA, same study] Shorts whose average view duration exceeded 50 seconds averaged 4.1 million views. "Just making a long short isn't the goal, making a short that holds people for as long as possible is." — [Galloway on X](https://x.com/PaddyG96/status/1646898363466481664)
- [OPINION] Vendor benchmarks for average percentage viewed disagree: one framework says 50-64% average, 65-79% strong, 80%+ viral; another puts the median Short near 45%; another sets the 2026 bar at 70%+. vidIQ reports 73.6% for its own Shorts (one channel). Bytecap states there is no official universal threshold. — [Bytecap](https://www.bytecap.io/research/youtube-shorts-retention-benchmarks); [vidIQ](https://vidiq.com/blog/post/increase-audience-retention-youtube/); [quso.ai](https://quso.ai/research/video-retention-benchmarks); [fluxnote](https://fluxnote.io/guides/good-audience-retention-youtube-shorts)
- [OPINION] Third-party blogs cite a 75-80% viewed benchmark; this figure is not from Galloway's study. — [subscribr](https://subscribr.ai/p/youtube-shorts-analytics-metrics-viral)
- [DATA/definition] Average percentage viewed can exceed 100% on Shorts because they auto-loop; above 100% means viewers are replaying. Shorter Shorts reach high percentages more easily, so the number is not comparable across lengths. — [CoSchedule](https://coschedule.com/social-media-marketing/youtube-metrics); [Bytecap](https://www.bytecap.io/research/youtube-shorts-retention-benchmarks)
- [OPINION, unverified] "Small test audience, then wider distribution" is widely described but is not in YouTube documentation. Claims that rewatches are "one of the highest-weighted signals" are unsourced. — [outlierkit](https://outlierkit.com/resources/youtube-shorts-algorithm/); [vidanalyze](https://vidanalyze.com/glossary/loop-rate)
- [DATA, 2026] Metricool (799,718 videos, 71,177 accounts, Feb 2025 vs Feb 2026): Shorts view duration fell 67% and interactions fell 15% year on year. The view-duration drop is very likely influenced by the March 2025 view-counting change, which adds many near-zero-duration views. — [Tubefilter](https://www.tubefilter.com/2026/08/03/youtube-shorts-long-form-study-metricool/); [Metricool](https://metricool.com/youtube-trends/)

### Inferences
- Pipeline targets (heuristic, not official): viewed rate at least 70%, treat under 60% as a failed hook; average percentage viewed at least 70%, with over 100% as the stretch goal for Shorts under about 30 seconds.
- Since March 2025 the public view count is inflated relative to older Shorts; evaluate with engaged views, viewed-vs-swiped and average percentage viewed, never raw views.
- Two separable gates follow from the metrics YouTube exposes: (a) the stay-or-swipe decision in the first second or two, (b) held duration afterwards. A pipeline should score these separately.

### Gaps
- No statement from Todd Beaupré specific to Shorts ranking was found in this session.
- The primary YouTube Help text and the Creator Insider video could not be read directly; wording above is via secondary coverage.
- No official word on how loops/rewatches are weighted. No large post-2025 replication of Galloway's VVSA thresholds was found.
- Retention Rabbit and Socialinsider data were not found.

## 2. Maximum length and best length for educational explainers

### Takeaway
Shorts can be up to 3 minutes (square or vertical, uploads from 15 October 2024). No reliable study identifies a best length; evidence says held duration matters more than length, with 30 to 60 seconds the common working range.

### Cited Findings
- [OFFICIAL] Announced 3 October 2024, effective 15 October 2024: square or vertical videos up to three minutes are classified as Shorts. Videos uploaded before that date are not reclassified. At launch the in-app camera could not record three-minute Shorts. — [TechCrunch](https://techcrunch.com/2024/10/03/youtube-takes-on-tiktok-with-longer-shorts-templates-trends-and-more); [Tubefilter](https://www.tubefilter.com/2024/10/03/youtube-shorts-three-minutes-max-length-redesign-trends-templates/); [9to5Google](https://9to5google.com/2024/10/03/youtube-shorts-3-minutes/)
- [OFFICIAL, DATED 2023] Todd Sherman advised prioritising storytelling over aiming at a particular duration. — [Search Engine Journal](https://www.searchenginejournal.com/youtube-explains-how-shorts-algorithm-works/494953/)
- [DATA, DATED 2023, 60-second cap era] Galloway: most creators made Shorts of 20 to 40 seconds; longer Shorts did much better on average. Secondary write-up gives average views by length: 50-60s 4.1M, 40-50s 1.8M, 30-40s 1.3M, sharply lower under 30s. Galloway's takeaway: the algorithm appears to favour 40s+ Shorts that hold viewer duration, with outliers at every length. — [Galloway on X](https://x.com/PaddyG96/status/1646898359808974851?lang=en); [LinkedIn](https://www.linkedin.com/posts/paddy-galloway-459b8913a_we-studied-33-billion-views-to-decode-the-activity-7053697597592498176-PAkf); [The Creator Buzz](https://www.thecreatorbuzz.com/p/youtube-shorts-study-worth-checking)
- [DATA, survey of beliefs, 2026] Adobe Express creator survey: an average of 46% of creators named under 30 seconds as ideal for views, shares, click-throughs and saves; for subscriber growth it split 34% under 30s versus 34% 30-60s. This is self-reported opinion, not measured performance. — [Adobe Express](https://www.adobe.com/express/learn/blog/youtube-shorts-length-study)
- [DATA, Oct 2026] OverseerOS: 16,537 short-form YouTube videos across 1,137 channels; conclusion as summarised: shorter videos can perform well but length alone does not explain success. Bucket-level numbers not retrieved. — [OverseerOS](https://www.overseeros.com/blog/best-youtube-shorts-length)
- [DATA, production not performance] OpusClip creation data clusters most heavily at 30-60 seconds. — [Bytecap](https://www.bytecap.io/research/best-youtube-shorts-length)

### Inferences
- Default explainer length: 30 to 60 seconds, as long as the topic needs and no longer. Use over 60 seconds only when the concept cannot be held together in less; no data found shows 1-3 minute Shorts outperforming.
- Galloway's "longer is better" result predates both the 3-minute cap and the view-counting change, and partly reflects that total watch time rises with length when retention holds. Apply it as "do not truncate a strong 50-second explanation to 20", not "pad".

### Gaps
- No performance data found for 60-180 second Shorts, nor any length data specific to educational content.
- Unverified: whether 1-3 minute Shorts get different feed treatment or music-licensing limits (commonly asserted; not confirmed here).

## 3. Hook structures for the first 1 to 3 seconds

### Takeaway
No official or rigorous data ranks hook types; the consistent practitioner principle is to deliver or promise specific value before the first sentence ends rather than delaying it. Viewed-vs-swiped is the direct measure of the hook.

### Cited Findings
- [OPINION] The first 2-3 seconds decide stay or leave; weak hooks "delay value rather than deliver it immediately". — [OpusClip](https://www.opus.pro/blog/youtube-shorts-hook-formulas)
- [OPINION] Lowest swipe-away hooks answer "why should I spend the next 30 seconds here?" before the first spoken sentence is finished. — [blitzcutai](https://blitzcutai.com/blog/best-youtube-shorts-hooks-2026)
- [OPINION] Wording patterns offered for educational Shorts: numbered lesson ("3 mistakes I made before..."); specific outcome ("I cut my editing time from 3 hours to 20 minutes with one tool"); correction ("You're writing hooks wrong. Here's what to say in the first 3 seconds instead"); bold counter-claim against a widely held belief; specific number in the promise. — [ugccopilot](https://ugccopilot.ai/hooks/youtube-shorts/); [faceless.so](https://faceless.so/blog/9-video-hook-formulas-that-stop-the-scroll-1); [OpusClip](https://www.opus.pro/blog/youtube-shorts-hook-formulas)
- [OPINION, unverified numbers] One source claims numbered-lesson and specific-outcome hooks score highest on Shorts (46-56% band) and that Shorts audiences favour educational hooks; method not published. — [ugccopilot](https://ugccopilot.ai/hooks/youtube-shorts/)
- [OPINION] Diagnostic: if the retention graph drops in the first 2-3 seconds, the hook is the problem. — [blitzcutai](https://blitzcutai.com/blog/best-youtube-shorts-hooks-2026)
- [OPINION] The first frame doubles as the effective thumbnail for a Short. — [postigniter](https://postigniter.com/blog/viral-youtube-shorts-titles-50-formulas-and-an-ai-generator-workflow)
- [Flagged unreliable] A "2-second algorithm filter" and "MrBeast uses pattern interrupts in 89% of openings" appear in a single unsourced post. Do not use. — [dev.to](https://dev.to/bulkdl/hook-engineering-the-science-of-the-first-3-seconds-in-short-form-video-2026-2jp9)

### Inferences
- For an animated maths/science explainer the analogues are: counterintuitive claim ("This sum of positive numbers equals a negative one"), question with stakes ("Why can't you comb a hairy ball flat?"), and showing the finished visual payoff in frame one then rewinding. These are adaptations, not sourced examples.
- Rules a pipeline can enforce: no title card, logo or greeting; frame 0 already contains motion plus the on-screen claim; the first spoken sentence states the question or claim; the hook must be honest, since an unpaid promise hurts held duration.

### Gaps
- No controlled comparison of hook types (question vs claim vs payoff-first vs open loop) was found for Shorts, educational or otherwise.

## 4. Pacing: visual change rate, narration speed, on-screen text

### Takeaway
Pacing numbers are folklore: vendors quote a visual change every 1.5 to 4 seconds and disagree with each other; nothing official or peer-reviewed was retrieved.

### Cited Findings
- [OPINION, conflicting] High-performing Shorts average one cut every 2-4 seconds (OpusClip); TikTok target of a cut every 3-4 seconds (blitzcutai); a visual change every 1.5-2 seconds for videos under 60 seconds (aibrify). No checkable dataset behind any of them. — [OpusClip](https://www.opus.pro/blog/ideal-youtube-shorts-length-format-retention); [blitzcutai](https://blitzcutai.com/blog/3-second-rule-tiktok-jump-cuts); [aibrify](https://aibrify.com/blog/short-form-video-editing-captions-b-roll-guide)
- [OPINION] Captions: 2-4 words on screen at a time. — [aibrify](https://aibrify.com/blog/short-form-video-editing-captions-b-roll-guide)
- [DATA, not retrieved] An academic marketing paper (Dost, Huang) tests jump-cut styles and transition frequency on short-form engagement; findings were not accessible. — [Marketing Trends Congress paper](https://archives.marketing-trends-congress.com/2026/pages/PDF/paper_professor_DOST_HUANG.pdf)
- [DATA claim, unverified] OpusClip advertises editing tips based on 13.5M clips. — [OpusClip](https://www.opus.pro/blog/video-editing-tips)

### Inferences
- Reasonable default: something meaningful changes on screen at least every 2 to 4 seconds (new object, transform, highlight, camera move, caption change). For continuous animation, a transform counts as a change; hard cuts are not required.
- Avoid static holds longer than about 4 seconds, especially in the first 10 seconds.

### Gaps
- No sourced words-per-minute figure for Shorts narration was found. Any WPM rule would be an assumption.
- No data on pattern-interrupt frequency, or on burned-in captions' effect on Shorts retention specifically.

## 5. Endings and looping

### Takeaway
Loops inflate average percentage viewed and every replay now counts as a view, but YouTube has confirmed no ranking weight for rewatches and offers no loop metric. Loop design advice is entirely practitioner opinion.

### Cited Findings
- [OFFICIAL] Since 31 March 2025 each replay of a Short counts as another view. — [Tubefilter](https://www.tubefilter.com/2025/03/26/youtube-shorts-views-counting-stats/); [Social Samosa](https://www.socialsamosa.com/news-2/youtube-shorts-now-count-every-play-replay-view-8895256)
- [Fact about tooling] YouTube Analytics has no "loop rate" metric; proxies are average view duration and average percentage viewed over 100%. — [vidanalyze](https://vidanalyze.com/glossary/loop-rate)
- [OPINION] Loop techniques: final frame flows into the first frame; audio continues across the cut; last line calls back a word or idea from the hook. — [vidIQ](https://vidiq.com/blog/post/double-youtube-watch-time-looping-shorts); [virvid](https://virvid.ai/blog/looping-structure-shorts-retention-2026)
- [OPINION, unverified] Claims that ranking weights replay rate above raw watch time "per Google guidance" could not be traced to any YouTube source. — [dev.to](https://dev.to/kyle_clipspeedai/reverse-engineering-the-youtube-shorts-algorithm-in-2026-signals-ml-and-what-actually-moves-the-ep6)

### Inferences
- End on the payoff and stop: no outro, no "thanks for watching", no trailing silence, since any dead tail lowers average percentage viewed and gives a swipe cue.
- A cheap loop for animations: make the final frame match the opening frame's composition, and phrase the last line so it leads back into the opening question.
- Place the main payoff late (final 10-20%) but give intermediate mini-payoffs so viewers are not just waiting.

### Gaps
- No data found on end-of-Short calls to action (subscribe prompts, "watch the full video") and their effect on retention or rewatch.
- No measurement of how much seamless loops raise rewatch rate.

## 6. Titles, captions, hashtags, posting frequency

### Takeaway
Officially (2023): hashtags are optional, posting volume gives no boost, time of day matters little, and delete-and-reupload is discouraged. Metricool's 2026 data puts the best per-Short performance at 2 to 4 Shorts per week.

### Cited Findings
- [OFFICIAL, DATED 2023] Todd Sherman: nothing in the algorithm expands or reduces reach for posting more Shorts, though posting many low-quality clips can hurt; hashtags help categorise topics and should be used in moderation "but are by no means necessary to reach people"; time of day matters less than responding quickly to timely topics; deleting and re-uploading an underperforming Short is "not a great strategy". — [Social Media Today](https://www.socialmediatoday.com/news/youtube-advice-for-shorts-creators-hashtags-algorithm/691835/); [Benzinga](https://benzinga.com/z/34020532); [MediaPost](https://www.mediapost.com/publications/article/388588/youtube-shorts-product-lead-offers-insights-for-cr.html)
- [DATA, 2026] Metricool: individual Shorts perform best at 2-4 uploads per week; total monthly views keep rising at higher frequencies. — [Tubefilter](https://www.tubefilter.com/2026/08/03/youtube-shorts-long-form-study-metricool/); [Metricool](https://metricool.com/how-to-grow-on-youtube/)
- [OPINION, conflicting] Vendor posting advice ranges from 3-5 per week to 1-3 per day. — [subscribr](https://subscribr.ai/p/how-often-to-post-youtube-shorts); [crayo](https://crayo.ai/blog/how-many-youtube-shorts-should-i-post-a-day)
- [OPINION] Titles: 40-70 characters (another says under 60), main keyword front-loaded because titles truncate, short and immediately clear rather than keyword-stuffed; a title that overpromises lowers watch time. — [clipspeed](https://www.clipspeed.ai/blog/youtube-shorts-seo-optimization-guide.html); [fluxnote](https://fluxnote.io/guides/how-to-write-youtube-shorts-titles); [subscribr](https://subscribr.ai/p/youtube-shorts-titles-thumbnails-matter)
- [OPINION, unverified] Claims of a 30-50% lift from numbers in titles come from single vendor blogs. — [postigniter](https://postigniter.com/blog/viral-youtube-shorts-titles-50-formulas-and-an-ai-generator-workflow)

### Inferences
- Titles do little for the feed (viewers there decide on the video itself) but matter for search, which is where Shorts get long-tail views; write a plain, searchable statement of the question the Short answers.
- Use zero to three specific topical hashtags; they are not a lever.
- Cadence rule: 2-4 per week, never trading quality for volume.

### Gaps
- No 2025-2026 official restatement of Sherman's 2023 points was retrieved; they may be stale.
- No data on description text, the related-video link, or burned-in versus auto captions for Shorts.
- Buffer and TubeBuddy studies could not be accessed.

## 7. Differences from TikTok and Instagram Reels

### Takeaway
All three now count a view on play start; the claimed distinguishing feature of Shorts is search-driven longevity, but this rests on vendor blogs rather than platform documentation.

### Cited Findings
- [OFFICIAL] The March 2025 change aligned Shorts view counting with TikTok and Reels (view on play/replay). — [eMarketer](https://emarketer.com/content/youtube-shorts-changes-view-count-rules-match-tiktok--instagram); [TechCrunch](https://techcrunch.com/2025/03/26/youtube-is-changing-how-youtube-shorts-views-are-counted)
- [OFFICIAL context] The 3-minute limit made Shorts compatible with longer vertical uploads already allowed on TikTok and Reels. — [TechCrunch](https://techcrunch.com/2024/10/03/youtube-takes-on-tiktok-with-longer-shorts-templates-trends-and-more)
- [OPINION] Shelf life: Shorts can collect views for months or years via search; TikTok days to weeks; Reels peak in 24-48 hours. — [shortsync](https://www.shortsync.app/resources/youtube-shorts-vs-tiktok-vs-reels-2026); [mediacube](https://mediacube.io/en/blog/shorts-vs-tiktok-vs-reels)
- [OPINION] TikTok has the strongest cold-start reach for new accounts; Shorts leans more on channel history and topic consistency; Reels leans on existing followers plus Explore. — [fluxnote](https://fluxnote.io/guides/youtube-shorts-vs-tiktok-algorithm-2026); [posteverywhere](https://posteverywhere.ai/blog/tiktok-vs-instagram-reels-vs-youtube-shorts)
- [OPINION, unconfirmed] A 2026 Shorts search filter/popularity sort is reported by vendor blogs only. — [easyviral](https://easyviral.ai/api/blog-md/youtube-shorts-vs-tiktok-vs-reels-faceless-creators-2026)

### Inferences
- Evergreen educational explainers fit Shorts well: they answer stable search queries, so searchable titles and topic consistency per channel are worth more here than on TikTok.

### Gaps
- No official cross-platform comparison of ranking signals exists; TikTok "200-500 user test batch" figures are unsourced.
