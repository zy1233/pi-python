export type Level = 'stable' | 'beta' | 'alpha';

export type PackageId = 'core' | 'harness' | 'cli' | 'zypi' | 'webAccess' | 'goalX' | 'workflows' | 'mcp';
export type PrincipleId = 'contract' | 'protocol' | 'noClient' | 'permission' | 'trust' | 'models';
export type EventId =
  | 'agentStart'
  | 'turnStart'
  | 'userMsg'
  | 'asstStart'
  | 'update'
  | 'asstEnd'
  | 'toolStart'
  | 'toolEnd'
  | 'toolResult'
  | 'turnEnd'
  | 'agentEnd';
export type RuleId = 'parallel' | 'terminate' | 'noRaise' | 'usage';
export type LayerId = 'zypi' | 'editors' | 'cli' | 'harness' | 'core' | 'models' | 'extensions';
export type FeatureId = 'tools' | 'sessions' | 'headless' | 'context' | 'extensions' | 'sandbox' | 'limits' | 'thinking';
export type TabId = 'tui' | 'zed' | 'headless' | 'python';
export type FooterLinkId =
  | 'github'
  | 'releases'
  | 'changelog'
  | 'issues'
  | 'design'
  | 'tuiGuide'
  | 'windows'
  | 'benchmark'
  | 'acp'
  | 'pi';

export interface Item {
  title: string;
  body: string;
}

export interface TabText {
  label: string;
  heading: string;
  codeLabel: string;
  code: string;
  notes: string[];
}

/**
 * All text of the site for one language. The English file and the Chinese file
 * must both satisfy this type, so a missing string fails the build.
 *
 * Inline markup in a string: `code` becomes <code>, **text** becomes <strong>.
 */
export interface Messages {
  /** Value of the lang attribute of <html>. */
  lang: string;
  /** Language code for hreflang links. */
  hreflang: string;
  /** Locale for Open Graph. */
  ogLocale: string;
  meta: { title: string; description: string; ogAlt: string };
  skip: string;
  nav: {
    label: string;
    why: string;
    contract: string;
    how: string;
    features: string;
    install: string;
    status: string;
  };
  ui: {
    github: string;
    pypi: string;
    x: string;
    copy: string;
    copied: string;
    replay: string;
    themeToDark: string;
    themeToLight: string;
    langSwitchLabel: string;
    langSwitchText: string;
    home: string;
    newTab: string;
  };
  hero: {
    badge: string;
    badgeNote: string;
    titleBefore: string;
    titleEm: string;
    titleAfter: string;
    lead: string;
    ctaPrimary: string;
    ctaSecondary: string;
    installLabel: string;
    installCommand: string;
    note: string;
  };
  terminal: {
    label: string;
    title: string;
    tag: string;
    prompt: string;
    tagFree: string;
    tagAsked: string;
    say1: string;
    say2: string;
    caption: string;
  };
  principles: {
    eyebrow: string;
    title: string;
    lead: string;
    cards: Record<PrincipleId, Item>;
  };
  contract: {
    eyebrow: string;
    title: string;
    lead: string;
    listTitle: string;
    notes: Record<EventId, string>;
    rulesTitle: string;
    rules: Record<RuleId, Item>;
  };
  architecture: {
    eyebrow: string;
    title: string;
    lead: string;
    layers: Record<LayerId, Item>;
    linkAcp: string;
    linkModels: string;
    extensionsList: string[];
    licenseNote: string;
  };
  features: {
    eyebrow: string;
    title: string;
    lead: string;
    items: Record<FeatureId, Item>;
  };
  install: {
    eyebrow: string;
    title: string;
    lead: string;
    tabListLabel: string;
    tabs: Record<TabId, TabText>;
  };
  status: {
    eyebrow: string;
    title: string;
    lead: string;
    columns: { component: string; level: string; role: string; source: string };
    levels: Record<Level, string>;
    where: { pypi: string; releases: string };
    descriptions: Record<PackageId, string>;
    next: string;
  };
  evidence: {
    eyebrow: string;
    title: string;
    lead: string;
    stat: string;
    statLabel: string;
    statNote: string;
    readTitle: string;
    points: string[];
    cta: string;
  };
  open: {
    eyebrow: string;
    title: string;
    body: string;
    unofficial: string;
    ctaStar: string;
    ctaIssue: string;
    ctaChangelog: string;
  };
  footer: {
    tagline: string;
    groups: { project: string; docs: string; more: string };
    links: Record<FooterLinkId, string>;
    legal: string;
    built: string;
  };
  notFound: { title: string; body: string; back: string };
}
