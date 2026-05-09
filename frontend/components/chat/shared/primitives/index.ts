/**
 * Chat UI Primitives
 *
 * Reusable animation building blocks following emilkowal standards:
 * - Entrances: 150-200ms ease-out
 * - Exits: 100-150ms ease-in
 * - Press feedback: scale(0.97)
 * - Stagger: 30ms per item
 */

export { AnimatedPresence, type AnimatedPresenceProps } from './AnimatedPresence';
export { FadeSlide, type FadeSlideProps } from './FadeSlide';
export { SpringCard, type SpringCardProps } from './SpringCard';
export { StaggerContainer, StaggerItem, type StaggerContainerProps, type StaggerItemProps } from './StaggerContainer';
export { StreamingReveal, StreamingLine, type StreamingRevealProps, type StreamingLineProps } from './StreamingReveal';
export { PulseLoader, type PulseLoaderProps } from './PulseLoader';
export {
  SkeletonBlock,
  type SkeletonBlockProps,
} from './SkeletonBlock';
