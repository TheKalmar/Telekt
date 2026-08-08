# Frontend localization

The zero-build control plane uses `src/digital_company/static/i18n.js` as its
translation catalog. English (`en`) is the fallback locale and Serbian Latin
(`sr`) is included. The selected language is stored in the user's browser.

HTML elements use stable translation keys:

```html
<button data-i18n="action.save">Save settings</button>
<input data-i18n-placeholder="chat.placeholder">
```

Dynamic UI should add the same attributes before calling `I18N.apply(root)`.
Never branch application behavior based on translated text.

## Add another language

Add its messages to the catalog or register it from another loaded asset:

```javascript
I18N.registerLocale("de", "Deutsch", {
  "nav.overview": "Übersicht",
  "nav.operations": "Betrieb"
});
```

Missing keys automatically use English, so a new translation can be shipped
incrementally. Keep keys semantic (`config.models.open`), not based on the
English sentence. `I18N.t(key)` is available when code needs a translated
string, while `I18N.setLanguage(code)` changes the active locale immediately.
