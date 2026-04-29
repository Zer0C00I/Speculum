#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
BUILD_DIR="${ROOT_DIR}/build"
APPDIR="${BUILD_DIR}/Speculum.AppDir"
PYI_SPEC="${ROOT_DIR}/packaging/linux/speculum.spec"
APPIMAGE_TOOL="${APPIMAGE_TOOL:-${ROOT_DIR}/appimagetool.AppImage}"

rm -rf "${DIST_DIR}" "${BUILD_DIR}"

pyinstaller --noconfirm --clean "${PYI_SPEC}"

mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/lib/speculum"
mkdir -p "${APPDIR}/usr/share/metainfo"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"
cp "${ROOT_DIR}/packaging/linux/AppRun" "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"
cp "${ROOT_DIR}/packaging/linux/speculum.desktop" "${APPDIR}/speculum.desktop"
cp "${ROOT_DIR}/packaging/linux/speculum.svg" "${APPDIR}/speculum.svg"
cp "${ROOT_DIR}/packaging/linux/speculum.appdata.xml" "${APPDIR}/usr/share/metainfo/speculum.appdata.xml"
cp "${ROOT_DIR}/packaging/linux/speculum.desktop" "${APPDIR}/usr/share/applications/speculum.desktop"
cp "${ROOT_DIR}/packaging/linux/speculum.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/speculum.svg"

cp -a "${DIST_DIR}/speculum/." "${APPDIR}/usr/lib/speculum/"

cat > "${APPDIR}/usr/bin/speculum" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
APPDIR="$(cd "${HERE}/../.." && pwd)"
exec "${APPDIR}/usr/lib/speculum/speculum" "$@"
EOF
chmod +x "${APPDIR}/usr/bin/speculum"

if [[ ! -x "${APPIMAGE_TOOL}" ]]; then
  echo "Missing appimagetool at ${APPIMAGE_TOOL}" >&2
  exit 1
fi

ARCH="$(uname -m)"
export ARCH

"${APPIMAGE_TOOL}" "${APPDIR}" "${DIST_DIR}/Speculum-${ARCH}.AppImage"
