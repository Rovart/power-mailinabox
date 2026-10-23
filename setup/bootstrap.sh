#!/bin/bash
#########################################################
# This script is intended to be run like this:
#
#   curl -L https://raw.githubusercontent.com/Rovart/power-mailinabox/v61.6/setup/bootstrap.sh | sudo bash
#
#########################################################

# Are we running as root?
if [[ $EUID -ne 0 ]]; then
	echo "This script must be run as root. Did you leave out sudo?"
	exit 1
fi

SUPPORTED_OSES="* Debian 12 (bookworm)
* Debian 13 (trixie)
* Ubuntu 22.04 LTS (Jammy Jellyfish)
* Ubuntu 24.04 LTS (Noble Numbat)"

if [ ! -f /usr/bin/lsb_release ]; then
	# Try installing it (apt install lsb-release)
	if [ ! -f /usr/bin/apt-get ]; then
		echo "This operating system does not have apt-get! This means it is unsupported!"
		echo "This script must be run on a system running one of the following OS-es:"
		echo "$SUPPORTED_OSES"
		exit 1
	fi

	echo "Installing lsb-release to understand which operating system we're running..."
	apt-get -q -q update
	DEBIAN_FRONTEND=noninteractive apt-get -q -q install -y lsb-release < /dev/null
fi

if [ -z "$TAG" ]; then
	# Make sure we're running on the correct operating system
	OS_ID=$(lsb_release -is)
	OS_VER=$(lsb_release -rs)
	if  [[ "$OS_ID" == "Debian" && ( "$OS_VER" == 12* || "$OS_VER" == 13* ) ]] ||
		[[ "$OS_ID" == "Ubuntu" && ( "$OS_VER" == "22.04" || "$OS_VER" == "24.04" ) ]]
	then
		TAG=v61.6
	elif [[ "$OS_ID" == "Debian" && "$OS_VER" == "11" ]] ||
		[[ "$OS_ID" == "Ubuntu" && "$OS_VER" == "20.04" ]]
	then
		echo "We are going to install the last version of Power Mail-in-a-Box supporting $(lsb_release -ds)."
		echo "IF THIS IS A NEW INSTALLATION, STOP NOW, AND USE A SUPPORTED DISTRIBUTION INSTEAD (ONE OF THESE):"
		echo "$SUPPORTED_OSES"
		echo
		echo "IF YOU'RE UPGRADING THE BOX TO THE LATEST VERSION, UPGRADE YOUR OPERATING SYSTEM TO ONE OF"
		echo "THE RELEASES ABOVE AFTERWARDS AND RUN THIS SCRIPT AGAIN."

		while true; do
			read -p "Do you want to proceed? ([Y]es/[N]o) " yn < /dev/tty || { echo "Installation cancelled."; exit 1; }

			case $yn in
				Yes | Y | yes | y )
					break
					;;
				No | N | no | n )
					echo "Installation cancelled."
					exit 1
					;;
				* )
					;;
			esac
		done

		TAG=v60.5
	elif [ "$OS_ID" == "Debian" ] && [ "$OS_VER" == "10" ]; then
		echo "We are going to install the last version of Power Mail-in-a-Box supporting Debian 10 (buster)."
		echo "IF THIS IS A NEW INSTALLATION, STOP NOW, AND USE A SUPPORTED DISTRIBUTION INSTEAD (ONE OF THESE):"
		echo "$SUPPORTED_OSES"
		echo
		echo "IF YOU'RE UPGRADING THE BOX TO THE LATEST VERSION, PLEASE VISIT THIS PAGE FOR NOTES ON HOW TO"
		echo "UPGRADE YOUR SISTEM TO DEBIAN 11 (bullseye)"
		echo "https://power-mailinabox.net/buster-eol"

		while true; do
			read -p "Do you want to proceed? ([Y]es/[N]o) " yn < /dev/tty || { echo "Installation cancelled."; exit 1; }

			case $yn in
				Yes | Y | yes | y )
					break
					;;
				No | N | no | n )
					echo "Installation cancelled."
					exit 1
					;;
				* )
					;;
			esac
		done

		TAG=v56.5
	else
		echo "This script must be run on a system running one of the following OS-es:"
		echo "$SUPPORTED_OSES"
		exit 1
	fi
fi

# The repository to download Power Mail-in-a-Box from. It can be
# overridden (e.g. to test a fork) with the SOURCE environment variable.
if [ -z "$SOURCE" ]; then
	SOURCE=https://github.com/Rovart/power-mailinabox
fi

# Clone the Mail-in-a-Box repository if it doesn't exist.
if [ ! -d $HOME/mailinabox ]; then
	if [ ! -f /usr/bin/git ]; then
		echo Installing git . . .
		apt-get -q -q update
		DEBIAN_FRONTEND=noninteractive apt-get -q -q install -y git < /dev/null
		echo
	fi

	echo Downloading Mail-in-a-Box $TAG. . .
	git clone \
		-b $TAG --depth 1 \
		"$SOURCE" \
		$HOME/mailinabox \
		< /dev/null 2> /dev/null

	echo
fi

# Change directory to it.
cd $HOME/mailinabox

# Update it.
if [ "$TAG" != "$(git describe --tags)" ]; then
	echo Updating Mail-in-a-Box to $TAG . . .
	if [ "$(git remote get-url origin 2>/dev/null)" != "$SOURCE" ]; then
		git remote set-url origin "$SOURCE"
	fi
	git fetch --depth 1 --force --prune origin tag $TAG
	if ! git checkout -q $TAG; then
		echo "Update failed. Did you modify something in $(pwd)?"
		exit 1
	fi
	echo
fi

# Start setup script.
setup/start.sh

