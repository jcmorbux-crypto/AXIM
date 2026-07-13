# AXIM Product Design Constitution

Governing standard for every screen, workflow, card, button, and form in the UI Vision branch. Supersedes convenience, engineering preference, and "we already built it that way" whenever they conflict. Applies before writing code, not just at review time.

## The Ten Questions

Before implementing anything, answer all ten. Any "No" means redesign before writing code.

1. Would someone who has NEVER traded before understand this screen in less than 30 seconds?
2. If every label were removed, would the visual hierarchy still guide the user to the correct action?
3. Does this screen reduce fear or increase fear? Every screen should increase confidence.
4. Does the user immediately know: What this is? Why they should use it? What happens next?
5. Can the user confidently make a decision without opening documentation? If not, the screen has failed.
6. Is this presenting engineering options, or solving a user's problem? Always solve the user's problem.
7. Have we hidden unnecessary complexity? Expert functionality should exist but should never overwhelm a beginner.
8. Would Pocket Option make this easier? Would Robinhood make this simpler? Would Apple remove something? Learn from those questions — adopt the product thinking, do not copy the designs.
9. Is there too much text? If an animation, graphic, simulator, or visual explanation can replace text, use the visual explanation.
10. If someone opened AXIM Trader for the very first time, would they think "This feels powerful" or "This feels complicated"? The correct answer is "This feels incredibly easy."

## The AXIM Standard

Every screen should feel: Effortless. Confident. Premium. Educational. Fast. Elegant. Professional.

Never intimidating. Never cluttered. Never engineering-first. Never configuration-first. Always trader-first.

## The Final Design Question

Before committing every UI change: "If this were the first screen a new customer ever saw, would they immediately believe AXIM Trader is worth paying for every month?"

If the answer is not an immediate yes, do not ship it. Redesign until the answer becomes yes.

## Relationship to the existing Design Principles (P1-P10)

This constitution doesn't replace `UI_VISION_DESIGN_PRINCIPLES.md` — it's the enforcement layer on top of it. P2 ("the math is one click away, never zero") already pointed at question 9 and 7 here; this constitution makes it a hard gate instead of a guideline. Where an earlier explicit instruction (e.g. "show the exact numbers on strategy cards, not just risk labels") pushes toward more density, the resolution is: keep the real numbers — never replace them with vague marketing labels, that's a separate non-negotiable (P9's honesty rule in the research report) — but the *deepest* mechanics (ladder sequences, milestone tables, reset conditions) still belong one tap away, not dumped on the first screen a beginner sees.
