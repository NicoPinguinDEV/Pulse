// U-03: build config for the Tailwind standalone CLI (v3.4.17, no Node needed) – see tools/build_css.py.
// Same settings as the former Play-CDN snippet (darkMode: 'class'); classes are read from the Python sources.
module.exports = {
  darkMode: 'class',
  content: ['./*.py', './cogs/*.py'],
  // stat_card() builds bg-{tint}-500/10 and text-{tint}-500 at runtime – the scanner cannot see those.
  safelist: [
    { pattern: /^(bg|text)-(slate|gray|red|rose|pink|fuchsia|purple|violet|indigo|blue|sky|cyan|teal|emerald|green|lime|yellow|amber|orange)-500(\/10)?$/ },
  ],
  theme: { extend: {} },
  plugins: [],
};
