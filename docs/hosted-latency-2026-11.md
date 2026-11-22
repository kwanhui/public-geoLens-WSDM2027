# Hosted latency, 22 November 2026

Eight queries to the hosted instance at <https://kwanhui-geo-lens.hf.space>,
sent one after another with a two-second pause, to measure how long the
encoder engines take on the Space's CPU. Every query named the five local
engines in `engines` (`contrastgeo`, `fewuser`, `retrievezero`,
`gazetteer_post`, `gazetteer_user`), so no text went to a third party and no
paid model was called. The four LLM engines came back skipped on every query,
every local engine reported a cost of 0, and the instance's spend counter
read the same before and after the run.

## The instance

`GET /instance` at 2026-11-22 10:25:38 UTC (18:25:38 SGT) reported version
0.14.5 and API version 1.0, and listed the same five engines as
`local_engines`. The deployed build ran the code of tag `v0.14.5`; the
commits deployed after that tag changed documentation, the evaluation
provenance file and `scripts/recompute_table.py`, none of which the server
runs. It answered in 727 ms, so the Space was running: the
first query below is the first encoder call of this run, not a start of the
container after a sleep.

## What was measured

`latency_ms` is the engine's own timing of its call on the server, as
returned in `per_engine`. "Round trip" is the time from sending the request
to receiving the whole response, measured on the client, so it also holds
the network, the other engines of the same request and the fusion. Times are
the client's clock at the moment each request was sent.

| # | Sent (UTC) | Sent (SGT) | ContrastGeo | FewUser | RetrieveZero | Gazetteer (post) | Gazetteer (user) | Round trip |
|---|---|---|---|---|---|---|---|---|
| 1 | 10:25:39 | 18:25:39 | 3,129 ms | 4,751 ms | 4,668 ms | 6.6 ms | 3.9 ms | 13,514 ms |
| 2 | 10:25:55 | 18:25:55 | 464 ms | 421 ms | 349 ms | 5.2 ms | 4.7 ms | 2,236 ms |
| 3 | 10:25:59 | 18:25:59 | 264 ms | 1,193 ms | 330 ms | 3.0 ms | 2.9 ms | 2,766 ms |
| 4 | 10:26:04 | 18:26:04 | 220 ms | 243 ms | 290 ms | 4.2 ms | 3.9 ms | 1,752 ms |
| 5 | 10:26:07 | 18:26:07 | 268 ms | 277 ms | 296 ms | 3.1 ms | 3.5 ms | 1,841 ms |
| 6 | 10:26:11 | 18:26:11 | 264 ms | 241 ms | 262 ms | 4.0 ms | 3.8 ms | 1,753 ms |
| 7 | 10:26:15 | 18:26:15 | 223 ms | 312 ms | 327 ms | 4.1 ms | 3.7 ms | 1,879 ms |
| 8 | 10:26:19 | 18:26:19 | 221 ms | 312 ms | 321 ms | 3.1 ms | 2.6 ms | 1,898 ms |

All eight returned HTTP 200 and a manifest naming version 0.14.5. All dates
are 2026-11-22.

## Summary

- First query: the three encoders took 3,129 to 4,751 ms, and the whole
  request 13.5 s.
- Queries 2 to 8: the three encoders took 220 to 1,193 ms (median 290 ms
  over the 21 calls), and the whole request 1.8 to 2.8 s. From query 4 on,
  every encoder call took 220 to 327 ms.
- The gazetteer took 2.6 to 6.6 ms on every query.

A start of the container after the Space has slept was not measured.

## The inputs

The texts are the demonstration scenarios' own.

| # | Post | Recent posts |
|---|---|---|
| 1, 2, 6, 8 | Massive fire at Marina Bay Sands! #Singapore #breaking | Best ramen in Shibuya tonight; Shinjuku always crowded these days; Got sake at a tiny bar in Asakusa |
| 3 | Lift at blk 118 Bidadari stuck again, third time this week | Banjir masih tinggi di Sintang, jembatan sudah tidak bisa dilewati; Need help, families stranded in Sintang, water has not gone down for days |
| 4 | Drain along Bidadari Park Drive overflowing after the morning downpour | the three Tokyo posts of query 1 |
| 5 | Air banjir di Sintang sudah masuk rumah, listrik mati sejak semalam | the two Sintang posts of query 3 |
| 7 | Anyone else at Bidadari BTO getting no water pressure since last night? | the two Sintang posts of query 3 |
