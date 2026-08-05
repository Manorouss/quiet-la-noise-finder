/**
 * Shared claim policy used by the browser contract and the Node admission scan.
 * A forbidden phrase is safe only when its own occurrence is immediately
 * preceded by not/never/no (with only a trivial determiner/copula in between).
 * Punctuation and conjunctions never carry negation to another occurrence.
 *
 * @param {string} text
 * @param {ReadonlyArray<string>} forbiddenPositiveFragments
 * @returns {boolean}
 */
export function claimTextIsSafe(text, forbiddenPositiveFragments) {
  const sentences = text.toLowerCase().split(/[.!?]+/).map((sentence) => sentence.trim()).filter(Boolean);
  return sentences.every((sentence) => forbiddenPositiveFragments.every((fragment) => {
    const escaped = fragment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const occurrences = new RegExp(`(?:^|[^a-z])(${escaped})(?=$|[^a-z])`, 'gi');
    let match;
    while ((match = occurrences.exec(sentence)) !== null) {
      const phraseStart = match.index + match[0].indexOf(match[1]);
      const prefix = sentence.slice(0, phraseStart).replace(/\s+$/, '');
      const explicitlyNegated = new RegExp(
        `(?:^|[^a-z])(?:not|never|no)(?:\\s+(?:a|an|the))?(?:\\s+(?:is|are|be))?\\s*$`,
        'i',
      ).test(prefix);
      // “not only …” cannot match the allowed determiner/copula sequence.
      if (!explicitlyNegated) return false;
    }
    return true;
  }));
}
