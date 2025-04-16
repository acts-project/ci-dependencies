#!/bin/bash
packages=`dpkg-query -Wf '${Installed-Size}\t${Package}\n' | sort -n | tail -n 25`

packages=`awk '{print $2}' <<< "$packages"`

packages_to_remove=("^dotnet-.*" "azure-cli" "^google-cloud-cli$" "^google-chrome-stable$" "firefox" "^powershell$" "mono-devel" "^temurin.*")

for package in $packages; do
    for pattern in "${packages_to_remove[@]}"; do
        if [[ "$package" =~ $pattern ]]; then
            echo "--> Uninstalling $package"
            sudo apt-get remove --purge $package
        fi
    done
done

sudo apt-get autoremove -y
sudo apt-get clean
rm -rf /usr/share/dotnet/
