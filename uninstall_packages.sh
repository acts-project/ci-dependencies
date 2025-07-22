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

rm -rf /home/packer
rm -rf /home/linuxbrew

pushd /home/runner || exit
rm -rf .rustup .cargo .dotnet

pushd /usr/local || exit
rm -rf aws-* julia* lib/android
popd || exit

pushd /usr/local/bin || exit
rm -rf azcopy cmake-gui helm minikube kustomize packer pulumi*
popd || exit

pushd /usr/local/share || exit
rm -rf chromium edge-driver chromedriver-* gecko_driver powershell vcpkg
popd || exit

