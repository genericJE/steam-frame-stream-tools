#!/usr/bin/bash
set -eu
echo "== before"; cat /etc/sysconfig/kernel
cp -a /etc/sysconfig/kernel /etc/sysconfig/kernel.bak-20261018
sed -i 's/^DEFAULTKERNEL=.*/DEFAULTKERNEL=kernel-zen-core/' /etc/sysconfig/kernel
echo "== after"; cat /etc/sysconfig/kernel
echo "== what the GRUB install hook will now see"; grub2-get-kernel-settings | grep -E "KERNEL_TYPE|NON_STANDARD|UPDATE_DEFAULT"
echo "== current default: $(grubby --default-kernel)"
