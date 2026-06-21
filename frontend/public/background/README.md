# Site background image

Drop a single image here named **`bg.jpg`** and it automatically becomes the
site-wide background (both the landing page and the Decision Coach page).

- Path the CSS looks for: `/background/bg.jpg` (this folder is served at the
  site root by Vite in dev and nginx in production).
- Supported: use a `.jpg` named exactly `bg.jpg`. To use a PNG/WebP instead,
  change the `url("/background/bg.jpg")` reference in `frontend/src/App.css`
  and `frontend/src/Home.css`.
- If no `bg.jpg` is present, nothing breaks — the page falls back to the plain
  dark gradient.

The image is intentionally masked behind the existing dark gradient (a
near-opaque `rgba(7, 8, 13, …)` overlay) so it reads as a subtle backdrop and
never washes out the foreground UI. To make the photo brighter or darker, tune
the alpha values of that `linear-gradient(...)` mask layer in the two CSS files.
