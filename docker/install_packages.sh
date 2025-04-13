if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "$ID" = "ubuntu" ]; then
    apt-get update
    apt-get install -y libvdt-dev ninja-build
    fi
fi