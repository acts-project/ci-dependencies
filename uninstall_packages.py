#!/usr/bin/env python3

import subprocess
import re
from glob import glob

packages = (
    subprocess.run(
        ["dpkg-query", "-Wf", "${Installed-Size}\t${Package}\n"],
        capture_output=True,
        check=True,
    )
    .stdout.decode()
    .strip()
    .split("\n")
)


def proc(line):
    size, name = line.split("\t")
    if len(size) > 0:
        size = int(size)
    else:
        size = 0
    return size, name.strip()


packages = [proc(line) for line in packages]
packages.sort(key=lambda pkg: pkg[0], reverse=True)
num = 50
print("Top", num, "heaviest packages:")
for _, (size, pkg) in zip(range(num), packages):
    print("-", pkg, f"{size/1e3}M")

packages_to_remove = []
remove_patterns = [
    "^dotnet-.*",
    "azure-cli",
    "^google-cloud-cli$",
    "^google-chrome-stable$",
    "firefox",
    "^powershell$",
    "mono-devel",
    "^temurin.*",
    "^mysql-server-core.*",
]

for _, pkg in packages:
    if any([re.search(p, pkg) for p in remove_patterns]):
        packages_to_remove.append(pkg)

print("Packages to remove:")
for pkg in packages_to_remove:
    print("-", pkg)

if len(packages_to_remove) > 0:
    subprocess.run(
        [
            "sudo",
            "apt-get",
            "remove",
            "--purge",
            "-y",
        ]
        + packages_to_remove,
        check=True,
    )

subprocess.run(["sudo", "apt-get", "autoremove", "-y"], check=True)
subprocess.run(["sudo", "apt-get", "clean"], check=True)

extra_files = [
    "/usr/share/dotnet/" "/home/packer",
    "/home/linuxbrew",
    "/home/runner/.rustup",
    "/home/runner/.cargo",
    "/home/runner/.dotnet",
    "/usr/local/aws-*",
    "/usr/local/julia*",
    "/usr/local/lib/android",
    "/usr/local/bin/azcopy",
    "/usr/local/bin/cmake-gui",
    "/usr/local/bin/helm",
    "/usr/local/bin/minikube",
    "/usr/local/bin/kustomize",
    "/usr/local/bin/packer",
    "/usr/local/bin/pulumi*",
    "/usr/local/share/chromium",
    "/usr/local/share/edge-driver",
    "/usr/local/share/chromedriver-*",
    "/usr/local/share/gecko_driver",
    "/usr/local/share/powershell",
    "/usr/local/share/vcpkg",
]

print("Extra files to remove:")
for extra in extra_files:
    for m in glob(extra):
        print("Removing", m)
        subprocess.run(["sudo", "rm", "-rf", m])
