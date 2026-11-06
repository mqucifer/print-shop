---
name: cutting-openings
description: Sizes an enclosure's openings, a plug's opening to the plug's body, a window or light pipe for an LED, vents and a fan's grille, and says how each is checked. Use when cutting any hole through a case's wall or lid, or deciding where its warm air leaves.
---

# Openings

Figures marked **a choice** have no source and are a starting point to say at the plan stop.

## A plug's opening

**Size it to the plug's body, not the port.** A plug's moulded grip is larger than the metal that goes into the port, and it has to pass through the wall to reach it. The request's writer draws the plug's body as a reference, pushed fully home in its port.

1. Cut the opening through the wall on the port's axis, the body's outline plus the clearance on each face: the request's, or 0.5 mm (`ref-fdm` [P1] [P2] [P3]).
2. If the port sits back from the wall's outside further than the body's metal reaches, recess the wall's outside round the opening, so the body seats.
3. Check: `clearance base.step references/plug.step --min-gap G`, the plug's body in place. `INTERFERE` means the body hits the wall; `TOO CLOSE` that it fits with too little room.
4. Round the opening's outside edge to the request's radius: a finger meets it, and `edges` reads it.

## An LED's light

An LED on a board inside has three ways out; which one is the request's, or a question at the plan stop.
- **An open hole** over the LED, on the line a person looks along. A choice: 2 to 3 mm across, which keeps a finger out (the test finger is 12 mm across).
- **A thin skin** of the case's own material over it, one or two layers (0.2 to 0.4 mm at 0.2 mm layers), which glows through a light colour and hardly at all through a dark one. A choice: whether it shows is an eye need, on a print.
- **A light pipe:** a clear rod printed apart, from just over the LED to the outside face, in a hole with sliding play (`ref-fdm` [P4], +0.15 mm a face).

Place it from the LED's position in the reference. A round hole is checked with `rays case.step --centre X,Y,Z --axis A --d D`, from a point on its axis.

## Vents and where warm air leaves

Warm air rises. Let cool air in low and warm air out high, or let a fan push it through: a fan in the lid blowing down onto a heatsink needs vents low in the walls for the air to leave by, and one blowing up needs them low to draw from.
- **Slots in an upright wall** are bridged at their top: keep each under 5 mm across, as a longer bridge sags ([E3]). A slot's rim narrower than a finger's tip isn't read by `edges`.
- **Holes over a fan's blades:** each under 12 mm across, so the test finger can't reach a blade; round, so `rays` can check one.
- Keep a vent off a board's components: `clearance` of the case to the board's reference reads the room.

## Sources

- [E3]: https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/ (bridges: "unless the bridge is less than 5mm"; overhangs printed "up to 45° without compromising quality")
- The test finger: IEC 60529's IPXX-B and IEC 61032's test probe B, 12 mm across, as `edges` uses it.
- `ref-fdm`'s [P1] to [P4], in the `references` skill.
