/** Lower case with the accents taken off: what two spellings of a search are compared as (« Été » finds « ete »). */
export const plain = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
