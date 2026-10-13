#!/bin/sh
set -eu

REPO="avyayv/outpost"
VERSION="${OUTPOST_VERSION:-main}"
BIN_DIR="${OUTPOST_BIN_DIR:-${HOME}/.local/bin}"
DATA_DIR="${OUTPOST_INSTALL_DIR:-${HOME}/.local/share/outpost}"

for command in curl tar python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'outpost installer: required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

if [ -e "$BIN_DIR/outpost" ] && [ ! -L "$BIN_DIR/outpost" ]; then
    printf 'outpost installer: refusing to overwrite %s\n' "$BIN_DIR/outpost" >&2
    exit 1
fi
mkdir -p "$BIN_DIR" "$(dirname "$DATA_DIR")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

archive="$tmp/outpost.tar.gz"
printf 'Downloading Outpost (%s)...\n' "$VERSION"
curl --fail --location --silent --show-error \
    "https://github.com/${REPO}/archive/refs/heads/${VERSION}.tar.gz" -o "$archive"
mkdir "$tmp/source"
tar -xzf "$archive" -C "$tmp/source" --strip-components=1

# Stage and swap the shared runtime; keep the CLI out of the repo checkout.
if [ -e "$DATA_DIR" ] && [ ! -f "$DATA_DIR/.outpost-install" ]; then
    printf 'outpost installer: refusing to replace an unowned directory: %s\n' "$DATA_DIR" >&2
    exit 1
fi
if [ -e "$DATA_DIR.previous" ]; then
    printf 'outpost installer: remove or inspect %s before updating\n' "$DATA_DIR.previous" >&2
    exit 1
fi
mkdir "$tmp/runtime"
cp -R "$tmp/source/api" "$tmp/source/tests" "$tmp/source/Dockerfile" "$tmp/source/compose.yaml" \
    "$tmp/source/.dockerignore" "$tmp/source/.gitignore" "$tmp/source/outpost" "$tmp/runtime/"
touch "$tmp/runtime/.outpost-install"
if [ -e "$DATA_DIR" ]; then mv "$DATA_DIR" "$DATA_DIR.previous"; fi
if ! mv "$tmp/runtime" "$DATA_DIR"; then
    if [ -e "$DATA_DIR.previous" ]; then mv "$DATA_DIR.previous" "$DATA_DIR"; fi
    exit 1
fi
rm -rf "$DATA_DIR.previous"

launcher="$tmp/outpost-launcher"
cat > "$launcher" <<EOF
#!/bin/sh
exec python3 "${DATA_DIR}/outpost" "\$@"
EOF
chmod 755 "$launcher"
install_tmp="$BIN_DIR/.outpost.$$"
cp "$launcher" "$install_tmp"
chmod 755 "$install_tmp"
mv -f "$install_tmp" "$BIN_DIR/outpost"

printf '\nOutpost installed to %s/outpost\n' "$BIN_DIR"
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) printf 'Add this to your shell profile so `outpost` is on PATH:\n  export PATH="%s:$PATH"\n' "$BIN_DIR" ;;
esac
printf '\nCreate an app in a new, empty directory:\n  mkdir my-api && cd my-api\n  outpost init\n'
