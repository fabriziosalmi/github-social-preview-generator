# github-social-preview-generator
#
# Nothing here needs the network except `make fonts`, which is a one-time
# bootstrap, and `make audit`, which reads public pages without credentials.

PYTHON  ?= python3
SRC     := src
PKG     := $(SRC)/gspg
FONTS   := $(PKG)/assets/fonts
VENDOR  := vendor/fonts
OUT     ?= previews
SITE    ?= site
RUN     := PYTHONPATH=$(SRC) $(PYTHON) -m gspg

FACES := InterDisplay-SemiBold Inter-Regular
PACKS := $(addprefix $(FONTS)/,$(addsuffix .glyphs.json,$(FACES)))

.DEFAULT_GOAL := help
.PHONY: help fonts glyphs verify-fonts build rebuild check audit coverage gallery \
        serve test lint clean distclean doctor

help: ## Show this help
	@echo "github-social-preview-generator"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  OUT=$(OUT)  SITE=$(SITE)"

## -- assets ----------------------------------------------------------------

fonts: ## Fetch the pinned upstream fonts, then rebuild the glyph packs (needs network, once)
	$(PYTHON) tools/vendor_fonts.py
	$(MAKE) glyphs

glyphs: $(PACKS) ## Rebuild the glyph packs from the vendored fonts

$(FONTS)/%.glyphs.json: $(VENDOR)/%.ttf tools/build_glyphpack.py tools/fontlib.py
	$(PYTHON) tools/build_glyphpack.py $< $(FONTS)

verify-fonts: ## Check the vendored fonts against the lock file
	$(PYTHON) tools/vendor_fonts.py --verify-only

## -- previews --------------------------------------------------------------

build: ## Render every preview listed in previews.json
	$(RUN) build --output $(OUT)

rebuild: ## Re-render everything, ignoring the lock file
	$(RUN) build --output $(OUT) --force

check: ## Verify the rendered output still matches previews.lock.json
	$(RUN) build --output $(OUT) --check

doctor: ## Report the local toolchain
	$(RUN) doctor

## -- coverage and publishing -----------------------------------------------

audit: ## Report which repositories still lack a custom preview (no credentials)
	$(RUN) audit --output $(OUT)

coverage: ## Write the coverage report to COVERAGE.md
	$(RUN) audit --output $(OUT) --markdown COVERAGE.md --json $(OUT)/coverage.json

gallery: ## Assemble the self-contained gallery site into site/
	$(RUN) gallery --output $(OUT) --site $(SITE)

serve: gallery ## Serve the gallery locally on port 8080
	@echo "http://127.0.0.1:8080/"
	@cd $(SITE) && $(PYTHON) -m http.server 8080

## -- quality ---------------------------------------------------------------

test: ## Run the test suite
	PYTHONPATH=$(SRC) $(PYTHON) -m unittest discover -s tests -v

lint: ## Byte-compile everything and check the house style
	$(PYTHON) -m compileall -q $(SRC) tools tests
	PYTHONPATH=$(SRC) $(PYTHON) tools/check_style.py

clean: ## Remove generated artefacts, keeping the committed previews
	rm -rf $(SITE) $(OUT)/scratch
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete

distclean: clean ## Also remove the rendered previews and vendored fonts
	rm -rf $(OUT)/png $(OUT)/svg $(VENDOR)
