import type { Config } from 'tailwindcss'
import { createPreset } from 'fumadocs-ui/tailwind-plugin';

const config: Config = {
  darkMode: ['class'],
  presets: [
    createPreset({
      cssPrefix: 'fd-', // Avoid conflicts with shadcn
    }),
  ],
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
    './content/**/*.{md,mdx}',
    './mdx-components.tsx',
    './node_modules/fumadocs-ui/dist/**/*.js',
  ],
  theme: {
  	extend: {
  		fontFamily: {
  			sans: ['var(--font-sans)'],
  			mono: ['var(--font-mono)'],
  		},
  		// emilkowal-style animation keyframes
  		keyframes: {
  			// === ENTRANCE ANIMATIONS (ease-out) ===
  			'fade-in': {
  				'0%': { opacity: '0' },
  				'100%': { opacity: '1' }
  			},
  			'fade-slide-up': {
  				'0%': { opacity: '0', transform: 'translateY(8px)' },
  				'100%': { opacity: '1', transform: 'translateY(0)' }
  			},
  			'fade-slide-down': {
  				'0%': { opacity: '0', transform: 'translateY(-8px)' },
  				'100%': { opacity: '1', transform: 'translateY(0)' }
  			},
  			'fade-slide-left': {
  				'0%': { opacity: '0', transform: 'translateX(8px)' },
  				'100%': { opacity: '1', transform: 'translateX(0)' }
  			},
  			'fade-slide-right': {
  				'0%': { opacity: '0', transform: 'translateX(-8px)' },
  				'100%': { opacity: '1', transform: 'translateX(0)' }
  			},
  			'scale-in': {
  				'0%': { opacity: '0', transform: 'scale(0.95)' },
  				'100%': { opacity: '1', transform: 'scale(1)' }
  			},
  			'scale-in-center': {
  				'0%': { opacity: '0', transform: 'scale(0.95)' },
  				'100%': { opacity: '1', transform: 'scale(1)' }
  			},
  			// === EXIT ANIMATIONS (ease-in, 100-150ms per emilkowal) ===
  			'fade-out': {
  				'0%': { opacity: '1' },
  				'100%': { opacity: '0' }
  			},
  			'fade-slide-out-down': {
  				'0%': { opacity: '1', transform: 'translateY(0)' },
  				'100%': { opacity: '0', transform: 'translateY(8px)' }
  			},
  			'fade-slide-out-up': {
  				'0%': { opacity: '1', transform: 'translateY(0)' },
  				'100%': { opacity: '0', transform: 'translateY(-8px)' }
  			},
  			'scale-out': {
  				'0%': { opacity: '1', transform: 'scale(1)' },
  				'100%': { opacity: '0', transform: 'scale(0.95)' }
  			},
  			// === FEEDBACK ANIMATIONS ===
  			'spring-press': {
  				'0%': { transform: 'scale(1)' },
  				'50%': { transform: 'scale(0.97)' },
  				'100%': { transform: 'scale(1)' }
  			},
  			'spring-bounce': {
  				'0%': { transform: 'scale(1)' },
  				'25%': { transform: 'scale(1.02)' },
  				'50%': { transform: 'scale(0.98)' },
  				'100%': { transform: 'scale(1)' }
  			},
  			'shimmer': {
  				'0%': { backgroundPosition: '-200% 0' },
  				'100%': { backgroundPosition: '200% 0' }
  			},
  			'check-draw': {
  				'0%': { strokeDashoffset: '24' },
  				'100%': { strokeDashoffset: '0' }
  			},
  			'shake': {
  				'0%, 100%': { transform: 'translateX(0)' },
  				'10%, 30%, 50%, 70%, 90%': { transform: 'translateX(-2px)' },
  				'20%, 40%, 60%, 80%': { transform: 'translateX(2px)' }
  			},
  			'countdown-pulse': {
  				'0%, 100%': { opacity: '1' },
  				'50%': { opacity: '0.5' }
  			},
  			'cursor-blink': {
  				'0%, 100%': { opacity: '1' },
  				'50%': { opacity: '0' }
  			},
  			// === STATUS ANIMATIONS ===
  			'spin-slow': {
  				'0%': { transform: 'rotate(0deg)' },
  				'100%': { transform: 'rotate(360deg)' }
  			},
  			'progress-indeterminate': {
  				'0%': { transform: 'translateX(-100%)' },
  				'100%': { transform: 'translateX(100%)' }
  			},
  			'pulse-ring': {
  				'0%': { transform: 'scale(1)', opacity: '1' },
  				'100%': { transform: 'scale(1.5)', opacity: '0' }
  			},
  			// === REVEAL ANIMATIONS ===
  			'reveal-up': {
  				'0%': { opacity: '0', transform: 'translateY(100%)' },
  				'100%': { opacity: '1', transform: 'translateY(0)' }
  			},
  			'typing': {
  				'0%': { width: '0' },
  				'100%': { width: '100%' }
  			}
  		},
  		animation: {
  			// Entrances (ease-out, 150-200ms)
  			'fade-in': 'fade-in 200ms ease-out',
  			'fade-in-fast': 'fade-in 100ms ease-out',
  			'fade-slide-up': 'fade-slide-up 150ms ease-out',
  			'fade-slide-down': 'fade-slide-down 150ms ease-out',
  			'fade-slide-left': 'fade-slide-left 150ms ease-out',
  			'fade-slide-right': 'fade-slide-right 150ms ease-out',
  			'scale-in': 'scale-in 200ms ease-out',
  			'scale-in-fast': 'scale-in 100ms ease-out',
  			// Exits (ease-in, 100-150ms)
  			'fade-out': 'fade-out 150ms ease-in',
  			'fade-out-fast': 'fade-out 100ms ease-in',
  			'fade-slide-out-down': 'fade-slide-out-down 100ms ease-in',
  			'fade-slide-out-up': 'fade-slide-out-up 100ms ease-in',
  			'scale-out': 'scale-out 150ms ease-in',
  			// Feedback
  			'spring-press': 'spring-press 150ms ease',
  			'spring-bounce': 'spring-bounce 200ms ease',
  			'shimmer': 'shimmer 2s linear infinite',
  			'check-draw': 'check-draw 300ms ease-out forwards',
  			'shake': 'shake 400ms ease',
  			'countdown-pulse': 'countdown-pulse 1s ease-in-out infinite',
  			'cursor-blink': 'cursor-blink 1s step-end infinite',
  			// Status
  			'spin-slow': 'spin-slow 2s linear infinite',
  			'progress-indeterminate': 'progress-indeterminate 1.5s ease-in-out infinite',
  			'pulse-ring': 'pulse-ring 1.5s ease-out infinite',
  			// Reveal
  			'reveal-up': 'reveal-up 200ms ease-out',
  			'typing': 'typing 2s steps(40, end)'
  		},
  		colors: {
  			border: 'hsl(var(--border))',
  			input: 'hsl(var(--input))',
  			ring: 'hsl(var(--ring))',
  			background: 'hsl(var(--background))',
  			foreground: 'hsl(var(--foreground))',
  			primary: {
  				DEFAULT: 'hsl(var(--primary))',
  				foreground: 'hsl(var(--primary-foreground))'
  			},
  			secondary: {
  				DEFAULT: 'hsl(var(--secondary))',
  				foreground: 'hsl(var(--secondary-foreground))'
  			},
  			destructive: {
  				DEFAULT: 'hsl(var(--destructive))',
  				foreground: 'hsl(var(--destructive-foreground))'
  			},
  			muted: {
  				DEFAULT: 'hsl(var(--muted))',
  				foreground: 'hsl(var(--muted-foreground))'
  			},
  			accent: {
  				DEFAULT: 'hsl(var(--accent))',
  				foreground: 'hsl(var(--accent-foreground))'
  			},
  			popover: {
  				DEFAULT: 'hsl(var(--popover))',
  				foreground: 'hsl(var(--popover-foreground))'
  			},
  			card: {
  				DEFAULT: 'hsl(var(--card))',
  				foreground: 'hsl(var(--card-foreground))'
  			},
  			chart: {
  				'1': 'hsl(var(--chart-1))',
  				'2': 'hsl(var(--chart-2))',
  				'3': 'hsl(var(--chart-3))',
  				'4': 'hsl(var(--chart-4))',
  				'5': 'hsl(var(--chart-5))'
  			}
  		},
  		borderRadius: {
  			lg: 'var(--radius)',
  			md: 'calc(var(--radius) - 2px)',
  			sm: 'calc(var(--radius) - 4px)'
  		}
  	}
  },
  plugins: [require("tailwindcss-animate"), require("@tailwindcss/typography")],
}
export default config
