module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs'],
  parser: '@typescript-eslint/parser',
  plugins: ['react-refresh'],
  rules: {
    // Context modules export hooks + providers; not worth splitting for a dev reference UI.
    'react-refresh/only-export-components': 'off',
    // Legacy API client and storage layers; tighten types incrementally.
    '@typescript-eslint/no-explicit-any': 'off',
    '@typescript-eslint/no-unused-vars': [
      'error',
      {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      },
    ],
    // Debug/PageIndex modals use intentional effect deps in this reference UI.
    'react-hooks/exhaustive-deps': 'off',
  },
}

