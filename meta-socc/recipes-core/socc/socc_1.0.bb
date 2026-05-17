DESCRIPTION = "SoC-Consistency — hardware constraint checker for Device Tree Sources"
HOMEPAGE    = "https://github.com/woo/SoC-Consistency"
LICENSE     = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=abc123placeholder"

SRC_URI = "https://files.pythonhosted.org/packages/soc-consistency.tar.gz"
SRCREV  = "${AUTOREV}"

S = "${WORKDIR}/soc-consistency"

inherit python3-pip

RDEPENDS:${PN} = "python3 python3-click python3-pyyaml"

do_install() {
    install -d ${D}${bindir}
    pip3 install --prefix=${D}${prefix} .
}

# Make socc available on the host (native) so meta-socc bbclass can call it
BBCLASSEXTEND = "native"
