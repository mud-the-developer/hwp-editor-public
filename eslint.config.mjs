import globals from 'globals';

export const correctnessRules = {
  'no-undef': 'error',
  'no-dupe-args': 'error',
  'no-dupe-keys': 'error',
  'no-dupe-class-members': 'error',
  'no-dupe-else-if': 'error',
  'no-unreachable': 'error',
  'no-unsafe-finally': 'error',
  'no-async-promise-executor': 'error',
  'no-constant-binary-expression': 'error',
  'no-unsafe-optional-chaining': 'error',
  'valid-typeof': 'error',
  'eqeqeq': ['error', 'always'],
  'no-eval': 'error',
  'no-implied-eval': 'error',
  'no-new-func': 'error',
};

const browserSafetyRules = {
  ...correctnessRules,
  'no-script-url': 'error',
  'no-restricted-properties': ['error', {
    object: 'document', property: 'write', message: 'Create DOM nodes and set textContent instead.',
  }],
  'no-restricted-syntax': ['error', {
    selector: "AssignmentExpression[left.type='MemberExpression']:matches([left.property.name='innerHTML'],[left.property.value='innerHTML'],[left.property.name='outerHTML'],[left.property.value='outerHTML']):not([right.type='Literal'][right.value=''])",
    message: 'Review/document text must use textContent. Only literal-empty HTML clearing is allowed.',
  }, {
    selector: "CallExpression:matches([callee.property.name='insertAdjacentHTML'],[callee.property.value='insertAdjacentHTML'])",
    message: 'Do not parse review/document text as HTML; create DOM nodes instead.',
  }],
};


export default [
  { linterOptions: { noInlineConfig: true } },
  { ignores: ['work/**', 'example/**', 'node_modules/**', '.venv/**'] },
  {
    files: ['scripts/**/*.mjs', 'tests/**/*.mjs', 'eslint.config.mjs'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module', globals: globals.node },
    rules: correctnessRules,
  },
  {
    files: ['web/**/*.inline.js'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'script', globals: globals.browser },
    rules: browserSafetyRules,
  },
  {
    files: ['native/HWPReview.Native/TerminalAssets/terminal.js'],
    languageOptions: {
      ecmaVersion: 'latest', sourceType: 'script',
      globals: { ...globals.browser, Terminal: 'readonly', FitAddon: 'readonly', UnicodeGraphemesAddon: 'readonly' },
    },
    rules: browserSafetyRules,
  },
  {
    files: ['native/HWPReview.Native/ViewerAssets/*.js'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module', globals: globals.browser },
    rules: browserSafetyRules,
  },
];
