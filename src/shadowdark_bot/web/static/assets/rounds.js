// Round boxes on an effect: filled boxes are rounds left (all filled at the
// start). The stored value stays `rounds_used`, so left = max - used.
//
// Tapping box n (1-based, left to right):
//   - a filled box spends ONE round from the right, whichever filled box was
//     tapped (n <= left  ->  left - 1);
//   - an empty box refills from the left up to and including itself (n > left
//     ->  left = n).
// Returns the new rounds_used.
export function usedAfterTap(max, used, n) {
  const left = max - used;
  return max - (n <= left ? left - 1 : n);
}
