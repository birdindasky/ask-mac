# Ask — make targets for dev / test / build / dmg.
#
# All targets assume `venv312/` is the build venv (Python 3.12, py2app +
# pyobjc + pywebview). The dev venv lives elsewhere and is not used here.

VENV_PY ?= venv312/bin/python
APP_NAME ?= Ask
DMG_NAME ?= $(APP_NAME)-0.2.0
DIST_DIR ?= dist

.PHONY: help dev test icon build alias dmg install clean nuke

help:
	@echo "Targets:"
	@echo "  make dev      — run the dev server (python run.py)"
	@echo "  make test     — pytest with the py3.12 build venv"
	@echo "  make icon     — regenerate assets/Ask.icns"
	@echo "  make alias    — fast alias build (dev iteration)"
	@echo "  make build    — full standalone .app in dist/"
	@echo "  make dmg      — build .app then wrap into a signed-less .dmg"
	@echo "  make install  — copy dist/$(APP_NAME).app to /Applications"
	@echo "  make clean    — remove build/dist artifacts (keep venv + assets)"
	@echo "  make nuke     — clean + drop the .iconset"

dev:
	python run.py

test:
	$(VENV_PY) -m pytest -q

icon:
	$(VENV_PY) scripts/build_icon.py

alias: clean icon
	$(VENV_PY) setup.py py2app -A

build: clean icon
	$(VENV_PY) setup.py py2app

dmg: build
	$(VENV_PY) scripts/build_dmg.py
	@echo "→ $(DIST_DIR)/$(DMG_NAME).dmg"

install: build
	rm -rf "/Applications/$(APP_NAME).app"
	cp -R "$(DIST_DIR)/$(APP_NAME).app" /Applications/
	@echo "Installed to /Applications/$(APP_NAME).app"

clean:
	rm -rf build $(DIST_DIR)

nuke: clean
	rm -rf assets/Ask.iconset assets/Ask.icns assets/Ask-1024.png
