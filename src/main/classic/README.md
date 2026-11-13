# Classic Komodo 9 icons

These are the application icons from Komodo IDE 9 (`share/icons`).  They are
installed as an alternative set next to the current icons:

- development tree: `dist/bin/chrome/icons/classic/`
- install image: `INSTALLDIR/share/icons/classic/`

The current Komodo icon remains the default application icon.  To use the
classic icon in a desktop shortcut, point its `Icon=` entry at the installed
classic icon, for example:

```ini
[Desktop Entry]
Name=Komodo IDE (classic icon)
Exec=/path/to/INSTALLDIR/lib/mozilla/komodo %F
Icon=/path/to/INSTALLDIR/share/icons/classic/komodo48.png
Terminal=false
Type=Application
```

Sizes provided: 16, 32, 48, 128, and 256 pixels, in both PNG and XPM formats.
