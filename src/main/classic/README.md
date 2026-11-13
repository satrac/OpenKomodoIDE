# Classic Komodo 9 icons

These are the application icons from Komodo IDE 9 (`share/icons`).  On
GTK/Linux they are the default application icons in this build; set
`ICON_STYLE=modern` to use the current Komodo icon instead.  Windows and
macOS keep their platform-specific icon formats (ICO/ICNS).

The active set is installed in:

- development tree: `dist/bin/chrome/icons/default/` and
  `dist/bin/chrome/komodo/skin/images/`
- install image: `INSTALLDIR/share/icons/`

The classic set is also always installed as an alternative:

- `dist/bin/chrome/icons/classic/`
- `INSTALLDIR/share/icons/classic/`

To select the classic icon explicitly for a desktop shortcut:

```ini
[Desktop Entry]
Name=Komodo IDE (classic icon)
Exec=/path/to/INSTALLDIR/lib/mozilla/komodo %F
Icon=/path/to/INSTALLDIR/share/icons/classic/komodo48.png
Terminal=false
Type=Application
```

Sizes provided: 16, 32, 48, 64, 128, and 256 pixels, in both PNG and XPM
formats.  The 64-pixel icon was generated from the 128-pixel original.
