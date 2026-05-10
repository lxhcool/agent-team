# Frontend Quality Rubric

Use this rubric when improving a complete page, complex component, dashboard, marketing section, admin surface, or app shell.

## 1. Information Architecture

A polished UI has a clear reading order:

- The user can tell what the screen is for within three seconds.
- Primary, secondary, and tertiary actions are visually distinct.
- Related metadata stays near the object it describes.
- Filters, tabs, search, and sort controls are placed where users expect them.
- Empty states explain what happened and what to do next.

## 2. Composition and Layout

Strong composition usually comes from restraint:

- Use a consistent content width and grid rhythm.
- Align content to shared edges.
- Create clear sections with spacing before adding lines or boxes.
- Use cards only when grouping is meaningful.
- Keep dense UIs scannable with clear row height, dividers, muted metadata, and stable controls.

## 3. Typography

Typography should clarify priority:

- Headings should be specific and not oversized by default.
- Body text should be comfortably readable and not too low contrast.
- Numeric values should align and scan cleanly in data-heavy views.
- Labels should be concise, consistent, and close to their controls.
- Long text must wrap, clamp, truncate, or scroll by design.

## 4. Color, Depth, and Surface

Use visual effects to clarify structure, not to decorate randomly:

- Prefer neutral surfaces with one restrained accent color.
- Use borders and background shifts before heavy shadows.
- Reserve bright color for actions, status, and meaningful emphasis.
- Avoid low-contrast text on tinted, blurred, or gradient backgrounds.
- Make status colors accessible and pair them with labels or icons.

## 5. Interaction Quality

Every interactive element should feel alive and reliable:

- Hover and active states should be visible but not dramatic.
- Focus-visible states must be obvious for keyboard users.
- Disabled states should explain or imply why an action is unavailable.
- Loading states should preserve layout to avoid jank.
- Errors should be specific, placed near the cause, and recoverable.
- Destructive actions should be clearly differentiated and protected when appropriate.

## 6. Overflow and Responsive Robustness

Assume real content is hostile:

- Long names, URLs, file paths, IDs, labels, and translations must not break layout.
- Add `min-w-0` to flex/grid children that need to shrink.
- Use `break-words` for unstructured text and `truncate` or `line-clamp` for bounded labels.
- Use `overflow-x-auto` only when horizontal scrolling is the best intentional solution.
- Avoid `w-screen` inside nested layouts because it often causes horizontal overflow.
- Avoid absolute positioning for core layout unless the container is carefully constrained.
- Ensure popovers, dropdowns, tooltips, and modals stay within the viewport.

## 7. Accessibility and Usability

Polish includes accessibility:

- Use semantic buttons, links, labels, headings, and regions.
- Preserve keyboard navigation and visible focus order.
- Do not rely on color alone for state.
- Keep hit targets comfortable.
- Respect reduced motion when adding transitions or animations.
- Maintain readable contrast across light and dark modes if both exist.

## 8. Code Review Smells

Treat these as signs that the UI needs improvement:

- Many arbitrary pixel values with no rhythm.
- Large fixed heights that crop content.
- Nested flex rows with no `min-w-0`.
- Cards inside cards inside cards with equal visual weight.
- Global gradients or decorative effects that compete with content.
- Buttons with similar emphasis but different importance.
- Missing empty, loading, error, disabled, and long-content states.
- Mobile layout treated as an afterthought.
