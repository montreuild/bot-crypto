import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Palette inspirée trading moderne (Bloomberg + Linear + Vercel)
        background: '#0a0e14',
        surface: '#0f1419',
        card: '#141a23',
        'card-hover': '#1a212c',
        border: '#1f2937',
        'border-hi': '#374151',
        foreground: '#e5e7eb',
        muted: '#9ca3af',
        dim: '#6b7280',
        // Accent colors
        primary: {
          50: '#ecfeff',
          100: '#cffafe',
          200: '#a5f3fc',
          300: '#67e8f9',
          400: '#22d3ee',
          500: '#06b6d4',
          600: '#0891b2',
          700: '#0e7490',
          800: '#155e75',
          900: '#164e63',
        },
        success: {
          DEFAULT: '#10b981',
          dim: 'rgba(16, 185, 129, 0.1)',
        },
        danger: {
          DEFAULT: '#ef4444',
          dim: 'rgba(239, 68, 68, 0.1)',
        },
        warning: {
          DEFAULT: '#f59e0b',
          dim: 'rgba(245, 158, 11, 0.1)',
        },
        purple: {
          DEFAULT: '#8b5cf6',
          dim: 'rgba(139, 92, 246, 0.1)',
        },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-jetbrains)', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'slide-down': 'slideDown 0.3s ease-out',
        'pulse-glow': 'pulseGlow 2s ease-in-out infinite',
        'flash-green': 'flashGreen 0.6s ease-out',
        'flash-red': 'flashRed 0.6s ease-out',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        slideUp: { '0%': { opacity: '0', transform: 'translateY(8px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        slideDown: { '0%': { opacity: '0', transform: 'translateY(-8px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        pulseGlow: {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(34, 211, 238, 0.4)' },
          '50%': { boxShadow: '0 0 0 8px rgba(34, 211, 238, 0)' },
        },
        flashGreen: {
          '0%': { backgroundColor: 'rgba(16, 185, 129, 0.3)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flashRed: {
          '0%': { backgroundColor: 'rgba(239, 68, 68, 0.3)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
