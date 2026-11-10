# Third-party components

The MIT license covers this project's own code. It does not replace the licenses of bundled dependencies. Release packages include dependency license texts under `legal/` (macOS: `Contents/Resources/legal/`).

| Component | License | Source |
| --- | --- | --- |
| CPython 3.12 | PSF license | https://www.python.org/downloads/source/ |
| lxml 5.3.0 | BSD-3-Clause; bundled libxml2/libxslt have their own notices | https://github.com/lxml/lxml/tree/lxml-5.3.0 |
| markdown-it-py 3.0.0 | MIT | https://github.com/executablebooks/markdown-it-py/tree/v3.0.0 |
| mdurl | MIT | https://github.com/executablebooks/mdurl |
| PySide6 / Shiboken6 6.8.3 | LGPLv3 / GPLv3 / commercial | https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/ |
| Qt 6.8.3 libraries used by PySide6 | LGPLv3 / GPLv3 / commercial; component-specific licenses | https://download.qt.io/archive/qt/6.8/6.8.3/single/ |
| PyInstaller 6.11.1 | GPLv2-or-later with bootloader exception | https://github.com/pyinstaller/pyinstaller/tree/v6.11.1 |

macOS packages use dynamically linked Qt libraries. You may replace or relink those libraries with compatible modified versions and reverse engineer the application for debugging such modifications, as permitted by LGPLv3. This project adds no restrictions on those rights. Modified bundles may require signing again with your own or an ad-hoc identity.

Corresponding source is available at the upstream links above; the pinned versions and build script in this repository identify the components used. LGPLv3 text: https://www.gnu.org/licenses/lgpl-3.0.html ; GPLv3 text: https://www.gnu.org/licenses/gpl-3.0.html . Dependency distributions' included licenses are copied into each native package by the build script.
