/**
 * Canonical transition tokens for interactive UI primitives.
 *
 * Per the design-system rules: never use `transition-all`; always specify
 * exact properties. The cubic-bezier curve is the project standard.
 */

export const INTERACTIVE_TRANSITION =
  "transition-[transform,opacity,box-shadow,colors,border-color] duration-200 ease-[cubic-bezier(0.2,0,0,1)]";

export const PRESS_SCALE = "active:scale-[0.96]";

export const HOVER_LIFT = "hover:shadow-md";

export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2";

export const INTERACTIVE_FEEDBACK = [
  INTERACTIVE_TRANSITION,
  HOVER_LIFT,
  PRESS_SCALE,
  FOCUS_RING,
].join(" ");
