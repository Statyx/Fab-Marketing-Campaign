/**
 * The four personas, ported from the V1 portal's registry (`portal/backend/main.py`).
 *
 * They are all backed by the *same* agent — in V1 the Fabric data agent, here the Foundry
 * supervisor. A persona is therefore framing, not a separate brain: it changes the welcome and
 * the suggested questions, never the source of the answer. Saying that out loud matters,
 * because four icons on a landing page imply four systems and there is only one.
 *
 * `expects` carries the source each suggestion is *expected* to reach. Expected, never
 * guaranteed: the agent picks its own route at runtime and the badge under the answer is the
 * only statement of what actually happened.
 *
 * `voc` and `mixed` were added after a plain observation: every question here used to be
 * `model` or `ontology`, and both of those live behind the *same* front door. The supervisor
 * holds two subordinates and the demo only ever exercised one — so for every question it was
 * literally a forty-second pass-through to the data agent, with nothing to cross-reference and
 * a prompt forbidding it to add anything of its own. A supervisor with one reachable source is
 * a relay. `mixed` questions are the ones that make it a supervisor: they need a size from the
 * semantic layer and a motive from the corpus, and putting those side by side is the only
 * output neither subordinate could have produced alone.
 *
 * The `voc` phrasings deliberately name no motive. The corpus was built so that one cause
 * dominates among the over-mailed cohort and is marginal elsewhere; a question naming it would
 * turn the discovery into a lookup, exactly as the prompt is forbidden from naming it.
 *
 * The `ontology` phrasings are copied verbatim from V1 rather than rewritten. They are the
 * formulations that were probed live and reached the graph 8/8 — and the note that came with
 * them is the reason not to touch them: the first 20 canned questions all *looked* relational
 * and none of them left the semantic model. A question that merely looks like a graph question
 * is not one.
 */

/**
 * Which back-end route a question is expected to take.
 *
 * `model` and `ontology` are both reached through the data subordinate; `voc` is the corpus
 * subordinate; `mixed` needs both and is the only kind that gives the supervisor real work.
 */
export type Source = 'model' | 'ontology' | 'voc' | 'mixed';

export interface Suggestion {
  q: string;
  expects: Source;
}

export interface Persona {
  key: string;
  name: string;
  description: string;
  icon: string;
  accent: string;
  welcome: string;
  suggestions: Suggestion[];
  /**
   * What this persona shows *before* anyone asks anything — the titles of the live panels it
   * owns in `PersonaPanels`.
   *
   * It exists so the landing card can advertise the data. The previous card counted the canned
   * questions by expected route ("2 modèle · 1 croisé"), which is a statement about the chat and
   * says nothing about the charts — and "there are charts" is precisely what the app was failing
   * to communicate. Kept in the registry rather than in the page so the claim sits next to the
   * persona it describes and cannot drift from the panels that back it.
   */
  panels: string[];
}

/** Named once. The diagnosis screen must never name it — there it has to emerge from the data. */
const CULPRIT = 'Black Friday Blast';

export const PERSONAS: Persona[] = [
  {
    key: 'direction',
    name: 'Direction',
    description: "Pilotage global : valeur du portefeuille, exposition à l'attrition, NPS",
    icon: '🎯',
    accent: '#00008F',
    panels: ['8 mesures du portefeuille', 'Répartition du risque'],
    welcome:
      "Bonjour, je suis l'assistant de pilotage de la relation client. Interrogez-moi sur le " +
      'chiffre d’affaires, la valeur du portefeuille, la part de clients à risque et la santé ' +
      'de la relation.',
    suggestions: [
      { q: "Quelle part de la base client est à risque d'attrition ?", expects: 'model' },
      { q: 'Combien de valeur vie client est exposée au churn ?', expects: 'model' },
      {
        q: 'Combien de clients sont en risque élevé ou critique, et que reprochent-ils dans leurs verbatims ?',
        expects: 'mixed',
      },
      { q: "Quel est le chiffre d'affaires total ?", expects: 'model' },
      { q: "Quel est le score d'attrition moyen par étape du cycle de vie ?", expects: 'model' },
      { q: 'Quel est le NPS moyen de la base ?', expects: 'model' },
      { q: 'Que disent les clients mécontents, dans leurs propres mots ?', expects: 'voc' },
      {
        q: `Quels comptes B2B regroupent le plus de clients touchés par la campagne ${CULPRIT} ?`,
        expects: 'ontology',
      },
    ],
  },
  {
    key: 'retention',
    name: 'Retention',
    description: 'Détection : la cohorte à risque, ses signaux et les clients à rappeler',
    icon: '🛟',
    accent: '#027180',
    panels: ['4 mesures de cohorte', 'Bandes de risque cliquables'],
    welcome:
      "Bonjour, je suis l'assistant rétention. Posez-moi vos questions sur les clients à " +
      'risque, leur récence, leur engagement, leurs désabonnements et la friction support.',
    suggestions: [
      { q: 'Combien de clients sont à risque et pour quelle valeur ?', expects: 'model' },
      { q: 'Quels clients dois-je rappeler en priorité ?', expects: 'model' },
      {
        q: 'Quels clients en risque élevé rappeler en priorité, et quel motif ressort de leurs échanges ?',
        expects: 'mixed',
      },
      { q: 'Quelle est la récence moyenne des clients à risque ?', expects: 'model' },
      { q: 'Combien de clients se sont désabonnés des emails ?', expects: 'model' },
      { q: 'Les interactions support négatives augmentent-elles le risque ?', expects: 'model' },
      { q: 'Sur quel ton les clients à risque écrivent-ils au support ?', expects: 'voc' },
      {
        q: "Quels comptes B2B concentrent le plus d interactions support négatives ?",
        expects: 'ontology',
      },
      { q: 'Quels clients à risque appartiennent au même compte B2B ?', expects: 'ontology' },
    ],
  },
  {
    key: 'marketing',
    name: 'Marketing',
    description: 'Diagnostic : pression email par campagne, désabonnements, engagement',
    icon: '📣',
    accent: '#896610',
    panels: ['Pression e-mail par campagne', 'Détection d’écart automatique'],
    welcome:
      "Bonjour, je suis l'assistant marketing. Interrogez-moi sur la pression commerciale par " +
      "campagne, les taux d'ouverture, de clic et de désabonnement, et la cause racine de " +
      "l'attrition.",
    suggestions: [
      { q: "Quelle campagne envoie le plus d'emails par client ?", expects: 'model' },
      {
        q: `Pourquoi la campagne « ${CULPRIT} » génère-t-elle autant de désabonnements ?`,
        expects: 'model',
      },
      { q: "Compare les taux d'ouverture entre campagnes", expects: 'model' },
      { q: 'Quel est le taux de désabonnement global ?', expects: 'model' },
      {
        q: 'Combien de désabonnements, et quelle raison les clients invoquent-ils eux-mêmes ?',
        expects: 'mixed',
      },
      { q: 'Que reprochent les clients qui reçoivent le plus d emails ?', expects: 'voc' },
      {
        q: "Quel segment concentre le plus d'envois et quel est son score de churn ?",
        expects: 'model',
      },
      {
        q: `Quels segments la campagne ${CULPRIT} ciblait-elle et quels segments a-t-elle réellement touchés ?`,
        expects: 'ontology',
      },
      {
        q: `Quels objets d email ont été utilisés par la campagne ${CULPRIT} ?`,
        expects: 'ontology',
      },
    ],
  },
  {
    key: 'commerce',
    name: 'Commerce',
    description: "Impact business : chiffre d'affaires, panier, attribution, retours",
    icon: '🛒',
    accent: '#863C41',
    panels: ['CLV et CA exposés', 'Exposition par segment'],
    welcome:
      "Bonjour, je suis l'assistant commerce. Posez-moi vos questions sur le chiffre " +
      "d'affaires, le panier moyen, les catégories de produits, l'attribution des campagnes " +
      'et les retours.',
    suggestions: [
      { q: 'Quelle catégorie de produit génère le plus de chiffre d’affaires ?', expects: 'model' },
      { q: 'Quel est le panier moyen par canal de vente ?', expects: 'model' },
      { q: "Quelle part du chiffre d'affaires est attribuée aux campagnes ?", expects: 'model' },
      { q: 'Quels sont les principaux motifs de retour ?', expects: 'model' },
      {
        q: 'Quels sont les motifs de retour, et comment les clients les formulent-ils ?',
        expects: 'mixed',
      },
      { q: 'Que disent les clients à propos du prix et de la qualité produit ?', expects: 'voc' },
      {
        q: `Quels produits ont été achetés par les clients touchés par ${CULPRIT} ?`,
        expects: 'ontology',
      },
      {
        q: 'Quelles catégories de produits les clients du segment High Value achètent-ils ?',
        expects: 'ontology',
      },
    ],
  },
];

export function personaByKey(key: string | undefined): Persona | undefined {
  return PERSONAS.find((p) => p.key === key);
}

/**
 * The order the starter questions should advertise the product in.
 *
 * A figure first — it is the fastest, the safest and it establishes that the numbers are real.
 * Then the graph, which is the capability nothing else in the demo shows. Then the cross-source
 * question, which is the only one neither subordinate could answer alone and therefore the only
 * one that justifies a supervisor at all. `voc` closes the list: a persona short of one of the
 * three above still gets a third chip rather than a hole.
 */
const FAMILY_ORDER: Source[] = ['model', 'ontology', 'mixed', 'voc'];

/**
 * Pick `n` suggestions spanning as many families as possible, in `FAMILY_ORDER`.
 *
 * Written because the cockpit capped its starters with `slice(0, n)` while this registry lists
 * every persona's numeric questions first and its graph questions last. The two are individually
 * reasonable and together they removed a whole capability from the product: no ontology question
 * was reachable from any persona's openers, so the graph stopped being demonstrable and nothing
 * failed to say so. A cap over an ordered list does not sample the list, it truncates it — if the
 * order carries meaning, the cap inherits it.
 *
 * Falls back to registry order once every family is represented, so `n` chips are always returned
 * when `n` suggestions exist.
 */
export function pickVaried(pool: Suggestion[], n: number): Suggestion[] {
  const picked: Suggestion[] = [];
  for (const family of FAMILY_ORDER) {
    if (picked.length >= n) break;
    const found = pool.find((s) => s.expects === family && !picked.includes(s));
    if (found) picked.push(found);
  }
  for (const s of pool) {
    if (picked.length >= n) break;
    if (!picked.includes(s)) picked.push(s);
  }
  return picked;
}
