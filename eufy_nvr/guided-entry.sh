#!/usr/bin/with-contenv bashio
set -o errexit
set -o nounset
set -o pipefail
umask 077

# Older pinned base images use the historical addon:: function name.
if declare -F bashio::app.ingress_port >/dev/null; then
    ingress_port="$(bashio::app.ingress_port)"
elif declare -F bashio::addon.ingress_port >/dev/null; then
    ingress_port="$(bashio::addon.ingress_port)"
else
    bashio::log.fatal "This app cannot read its Home Assistant setup port."
    exit 1
fi

if [[ ! "${ingress_port}" =~ ^[0-9]{5}$ ]] || \
   (( 10#${ingress_port} < 62000 || 10#${ingress_port} > 65500 )); then
    bashio::log.fatal "Home Assistant has not assigned a valid setup port."
    exit 1
fi

# No passwords or token values in command arguments or startup messages.
exec python3 /opt/eufy/bridge/guided_launcher.py --port "${ingress_port}"
