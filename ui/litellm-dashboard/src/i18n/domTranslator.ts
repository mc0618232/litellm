/**
 * DOM-level auto-translation.
 *
 * Most dashboard pages hard-code English strings, so instrumenting every
 * component with t() is impractical. Instead, when a non-English language is
 * active we walk the rendered DOM and replace text nodes (and a few
 * user-visible attributes) whose trimmed content exactly matches a dictionary
 * key. A MutationObserver keeps newly rendered content translated.
 *
 * Only exact matches are replaced, so user data (key aliases, model names,
 * emails) is never touched. Strings already translated pass through the
 * dictionary unchanged, which also prevents observer feedback loops.
 */

import { Language, translate } from "./translations";

const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "CODE", "PRE"]);
const TRANSLATED_ATTRS = ["placeholder", "title", "aria-label"];
const ATTR_SELECTOR = TRANSLATED_ATTRS.map((attr) => `[${attr}]`).join(",");

/**
 * Start translating the document into `language`. Returns a cleanup function
 * that stops observing and restores the original English text.
 */
export function startDomTranslation(language: Language): () => void {
  if (typeof document === "undefined" || language === "en" || !document.body) {
    return () => {};
  }

  // Originals kept for restore-on-cleanup. Strong refs are fine here: entries
  // are bounded by the amount of translated UI text on the page.
  const textOriginals = new Map<Text, string>();
  const attrOriginals = new Map<Element, Map<string, string>>();

  const translateTextNode = (node: Text) => {
    const raw = node.nodeValue;
    if (!raw) return;
    const parent = node.parentElement;
    if (parent && SKIP_TAGS.has(parent.tagName)) return;
    const trimmed = raw.trim();
    if (!trimmed) return;
    const translated = translate(language, trimmed);
    if (translated === trimmed) return;
    if (!textOriginals.has(node)) textOriginals.set(node, raw);
    node.nodeValue = raw.replace(trimmed, translated);
  };

  const translateAttributes = (el: Element) => {
    for (const attr of TRANSLATED_ATTRS) {
      const value = el.getAttribute(attr);
      if (!value) continue;
      const trimmed = value.trim();
      if (!trimmed) continue;
      const translated = translate(language, trimmed);
      if (translated === trimmed) continue;
      let saved = attrOriginals.get(el);
      if (!saved) {
        saved = new Map();
        attrOriginals.set(el, saved);
      }
      if (!saved.has(attr)) saved.set(attr, value);
      el.setAttribute(attr, translated);
    }
  };

  const walk = (root: Node) => {
    if (root.nodeType === Node.TEXT_NODE) {
      translateTextNode(root as Text);
      return;
    }
    if (!(root instanceof Element) && !(root instanceof Document)) return;
    if (root instanceof Element) {
      if (SKIP_TAGS.has(root.tagName)) return;
      translateAttributes(root);
    }
    root.querySelectorAll(ATTR_SELECTOR).forEach(translateAttributes);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      translateTextNode(node as Text);
    }
  };

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === "characterData") {
        translateTextNode(mutation.target as Text);
      } else if (mutation.type === "childList") {
        mutation.addedNodes.forEach(walk);
      } else if (mutation.type === "attributes" && mutation.target instanceof Element) {
        translateAttributes(mutation.target);
      }
    }
  });

  observer.observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: TRANSLATED_ATTRS,
  });
  walk(document.body);

  return () => {
    observer.disconnect();
    textOriginals.forEach((original, node) => {
      if (node.isConnected) node.nodeValue = original;
    });
    attrOriginals.forEach((attrs, el) => {
      if (el.isConnected) attrs.forEach((value, attr) => el.setAttribute(attr, value));
    });
  };
}
