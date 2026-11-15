# Surface: ironing and the seam

Distilled from OrcaSlicer's wiki, read 2026-11-13. The wiki is the source: each setting links to its page.

Both depend on which faces of the part will be seen, and that's the person's answer at the intake ("Which faces of this part will be seen?"), never which face is on top as it prints. A channel's floor is a top as it prints, but it's inside the part; a cap printed upside down hides its printed top. The review reads the slice for both and proposes a change when they don't fit the answer (mqucifer/print-shop#105).

## Ironing (`ironing_type`)
[OrcaSlicer wiki: Ironing, type](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_ironing#type)

- **Effect:** Which layers are ironed: a second pass at the same height with very little flow, to smooth a top surface. `top` irons all top surfaces, "the most common setting"; `topmost` only the last top layer of each object; `solid` every solid layer, internal solid infill included; `no ironing` none.
- **Trade-off:** More print time: `solid` "may increase print time significantly". The wiki also warns that ironing moves filament very slowly through the hotend, which risks heat creep and clogging.
- **Change it when:** the wiki says `solid` is for a very smooth finish on all solid surfaces, and `topmost` for prints where only the last layer needs smoothing. From the first real prints (2026-11-13): `top` ironed a channel's floor, inside the part, for about 12 minutes, and a cap's printed top, which ends up hidden. Iron only a top that is seen: `no ironing` when it's hidden, `topmost` when the part has other tops inside it.
- **Bound:** one of `no ironing`, `top`, `topmost`, `solid`, as Orca writes them. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Seam position (`seam_position`)
[OrcaSlicer wiki: Seam, seam position](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_seam#seam-position)

- **Effect:** Where each wall loop starts and ends. `aligned` lines the seams up at the least visible spots, such as concave corners; `back` puts them on the back (the layer's largest y); `aligned_back` combines the two, away from the front; `nearest` takes the point closest to the nozzle, which scatters them; `random` scatters them on purpose.
- **Trade-off:** The wiki says aligned, aligned back and back work best, especially with seam painting, and that random spreads the weak point across layers, which may suit a part that needs strength (a pin through a hole). A scattered seam leaves marks all over the wall; a line leaves one mark from bottom to top.
- **Change it when:** the walls are seen and the slice's seam is a line on a smooth wall (the review reads where each loop starts): move it to the back, or scatter it. On the first real prints, an aligned seam ran up a sign letter's walls, which are its whole look.
- **Bound:** one of `aligned`, `aligned_back`, `back`, `nearest`, `random`. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.
