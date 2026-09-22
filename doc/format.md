# The Bryton Rider route file format

What a `PlanTrip/*.fit` file contains, and what the Rider 420 makes of it.
Every claim below is measured on the reference pair in `tests/data/ex_bon/` and
checked by `tests/test_bryton.py`.

## Evidence

| File | Written by | Size |
| --- | --- | --- |
| `PlanTrip/De Uzer à Soyons.fit` | the Bryton Active app | 30 293 B |
| `Tracks/De Uzer à Soyons.{smy,tinfo,track}` | a Rider 420, firmware R057, from that file | 60 / 4 224 / 29 472 B |
| `Tracks/De Uzer à Soyons/{*.zinfo,sort1.path}` | the same device | 16 / 496 B |

One 81 km route with 1 842 points and 96 cues, seen both as input and as the
device's own output. This replaces the notes that came with BrytonUtilities,
which were written without a file from Bryton's software and got several
fields wrong; corrections are called out below.

## Container: standard FIT

The file is plain FIT. Bryton only swaps the documented `course` and
`course_point` messages for private global message numbers 248 to 254. The
bytes the old notes called "reserved" are FIT definition messages.

```
0      header size, 14
1      protocol version, 0x10
2-3    profile version, 2088
4-7    data size: every byte between header and trailing CRC
8-11   ".FIT"
12-13  CRC-16 of bytes 0-11
...    records
last 2 CRC-16 of header and records
```

Both CRCs are real in the app's file. The Rider also accepts zeros, so it does
not check them.

## Messages, in file order

| Local | Global | Content |
| --- | --- | --- |
| 7 | 248 | 2 × uint16, always `(0, 1)` |
| 1 | 254 | route summary |
| 2 | 251 | 1 × uint16 per point: the point index `0 … n−1` |
| 3 | 253 | cue count |
| 4 | 250 | one cue per record; definition re-issued when the label field grows |
| 5 | 252 | point count |
| 6 | 249 | one point per record |

### 254, summary

| # | Type | Meaning |
| --- | --- | --- |
| 1 | uint16 | point count |
| 2 / 3 | sint32 | northernmost / southernmost latitude |
| 4 / 5 | sint32 | easternmost / westernmost longitude |
| 6 | uint32 | total distance, metres |
| 7 / 8 | uint16 | maximum / minimum altitude, FIT scale |
| 9 / 10 | uint16 | total ascent / descent, plain metres |

Fields 9 and 10 were unknown to the old notes. Summing the elevation gain over
the 1 842 points gives 1 177 m up and 1 125 m down; the file stores 1 177 and
1 126, the difference being the 0.2 m altitude step.

### 250, cue

| # | Type | Meaning |
| --- | --- | --- |
| 1 | uint16 | index of the point the cue sits on |
| 2 | enum | manoeuvre code |
| 3 | uint32 | distance **to the next cue**, metres |
| 4 | uint32 | zero |
| 5 | string | road name, NUL terminated, at most 32 bytes of content |

Field 3 looks forward. Over the 95 cues that have a successor it matches the
along-route distance to the next cue within 17 m (−0.18 m on average), while
the distance since the previous cue is off by up to 8.8 km. The old encoder
wrote the latter, so every "in X m" prompt announced the road already ridden.

Field 4 is zero on turns too; the `FF FF FF FF` the old notes described is
FIT's "invalid" filler, not a turn marker.

Field 5 starts as a 32-byte field. When a name needs all 32 bytes plus its
terminator, the app re-issues the definition with a 33-byte field and keeps it
for the remaining cues: 5 cues at 32 bytes, then 91 at 33 in the reference.
Names are cut on a character boundary (`Rampe d'accès à la Via Ardèche` is
stored as `Rampe d'accès à la Via Ardèch`). UTF-8 accents are fine.

The first cue sits on point 0. The last is `0x21` on the final point, with
distance 0 and an empty name.

### Manoeuvre codes

Checked by correlating each code with the bearing change measured at its
point, over 40 m either side:

| Code | Count | Median change | Meaning |
| --- | --- | --- | --- |
| `0x01` | 4 | −5° | straight on |
| `0x02` | 20 | +59° | right |
| `0x03` | 25 | −60° | left |
| `0x04` | 6 | +52° | slight right |
| `0x05` | 4 | −23° | slight left |
| `0x06` | 2 | +91° | sharp right |
| `0x07` | 4 | −103° | sharp left |
| `0x08` | 13 | +25° | keep right |
| `0x09` | 3 | −5° | keep left |
| `0x21` | 1 | — | end of route |

Left and right are unambiguous for every code. `0xC9` and `0xD2`–`0xD5` also
occur (13 cues) and are not directions: `0xD2` turns right at all 3 of its
cues, `0xD3` (7 cues) anywhere from 97° left to 40° right, `0xD4` and `0xD5`
(1 each) bear left, `0xC9` (1) too. Roundabout exits 1 to 4 fit loosely, as a
French roundabout's first exit tends to be on the right; the track is too
sparse around them to tell more, and nothing confirms what the Rider shows for
them. They are never written.

A roundabout is written as a plain direction, with its exit number in front of
the street name: `(2) Rue Richelieu`. The direction runs from the way in to the
way out past the ring, measured on the route (`route.roundabout_angle`): the
ring is where the route turns by 7° or more every 5 m. Planners' own angles are
ignored. BRouter's `turn-angle` pointed left on all six roundabouts checked,
straight-on exits included, and Valhalla's bearings were wrong on all three of
its own. The measured direction matched the road on all five roundabouts of the
recorded Lyon test lines.

### 249, point

sint32 latitude and longitude in **microdegrees** (FIT would use
semicircles), uint16 altitude on the **standard FIT scale** `(m + 500) × 5`.

## What the Rider builds from it

* `.tinfo` — the cue table, 44 bytes per cue: uint16 point, uint16 code, uint32
  distance, uint32 zero, 32-byte name. Field for field equal to the `.fit`.
* `.track` — the points, 16 bytes each: microdegree latitude and longitude,
  altitude in **plain metres**, a zero.
* `.smy` — 60 bytes: `01 00`, the summary's point count, bounding box and
  distance, maximum altitude in metres, three unidentified bytes, `FE`, padding.
* `<name>/sort1.path` — 31 records of 4 × uint32: point index ranges and what
  look like map tile coordinates. Generating these would require Bryton's map
  tiling scheme; the device does it, which is why this project writes `.fit`
  files and lets the Rider produce the rest.
* `<name>/*.zinfo` — `[4, 13, 31, 496]`: the record count and byte size of
  `sort1.path` among them.

A file the firmware rejects produces no route entry and no message.

## Where planners put instructions in a GPX

| Planner and style | Geometry | Instructions |
| --- | --- | --- |
| BRouter, `osmand-style` | `<trk>` | `<rte><rtept>` with `<turn>` code, `<turn-angle>`, `<offset>` = track index |
| BRouter, `locus-style` | `<trk>` | `<sym>` on the track points themselves (`right_slight`, `pass_place`…) |
| BRouter, `gpsies-style` | `<trk>` | `<wpt>` with `<type>` code and lowercase `<sym>` |
| BRouter, mode 0 or 1 (profile default) | `<trk>` | none |
| OpenRouteService directions | `<rte>` | each `<rtept>` repeats its step's `<desc>` and numeric `<type>`, with a `<step>` index |
| PlotARoute, waypoints + track | `<trk>` | `<wpt>` with `<sym>` and an English sentence |
| PlotARoute, route only | `<rte>` | every `<rtept>` marked `Flag, Blue`; the manoeuvre is only in the French or English sentence |
| Komoot, Strava | `<trk>` | none |

The web clients (brouter.de/brouter-web, bikerouter.de) format the GPX in the
browser. With `osmand-style` they rewrite the `creator` to `OsmAndRouter`; with
the profile default (`auto-choose`) they write the track only, which a real
bikerouter.de export confirms. Their "Load Track as Route" reduces an uploaded
track to at most 200 waypoints and routes between them, which is how a
geometry-only GPX (Komoot, Strava) can gain instructions.

BRouter codes (from `btools.router.VoiceHint`): `C`, `TL`, `TSLL`, `TSHL`, `TR`,
`TSLR`, `TSHR`, `KL`, `KR`, `TU`/`TLU`/`TRU`, `RNDB<n>`/`RNLB<n>` for roundabout
exit *n*, `EL`/`ER`, and `BL`/`OFFR` which are not manoeuvres. In
`gpsies-style`, `KL` and `KR` are written with the slight-turn symbols.
