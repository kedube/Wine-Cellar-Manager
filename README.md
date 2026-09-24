# Wine Cellar Manager

This integration offers a tool and dashboard card to manage several wine cellars locally.

I strongly recommend using the card in its own dashboard, using the "Panel" layout (a single card on that dashboard).

![Wine Cellar](images/main_image.png)

I created the integration because no solution currently available did exactly what I wanted to do. The "Wine cellar" integration simply didn't work on my server, and the "Wine Tracker" app, while beautiful and full-featured, doesn't organize wines as a visual equivalent to their physical location. It's a tracker, not a cellar representation. Other apps, not part of Home Assistant, often rely on subscriptions or add unwanted features while not excelling at what I actually needed.

The benefit of Wine Cellar Manager, beyond its simplicity, lies in its ability to physically represent a cellar while showing sufficient information. I built it by thinking about what I actually want to do and how I like interacting with my physical and virtual cellar.

I created this integration first for my personal use, but I'm happy to share it with anyone interested. Use it as is, feel free to comment, and recommend new features or bug corrections!

## Installation and configuration

### Step 1: Install via HACS (Custom Repository)
Since this integration is not yet part of the HACS default store, you must add it as a Custom Repository:
1. In your Home Assistant interface, click on HACS in the sidebar.
2. Click on the three dots in the top right corner and select Custom repositories.
3. Paste the URL of this GitHub repository into the Repository field.
4. Select Integration in the Category dropdown menu, then click Add.
5. Find the newly added Wine Cellar Manager card in HACS, click on it, and select Download.
6. Restart Home Assistant to load the integration.

### Step 2: Set up the Integration
1. Go to Settings > Devices & Services in Home Assistant.
2. Click +Add Integration in the bottom right corner.
3. Search for Wine Cellar Manager and click on it to begin configuration.
4. The integration proposes default paths for label images which should not be changed without reason. 
5. Enter your Gemini API key and the Gemini model name when requested (see below).

Lastly, it asks for the name of the Gemini model. For now, `gemini-3.6-flash` is the most robust, offers free daily tokens (sufficient to build a reasonable cellar) and is more effective than newer models which only offer free tiers in their "lite" modes (which are not as effective). For now, the integration doesn't do anything with a different model, so just leave it as is.

### Step 3: Add the Lovelace Card
Once the integration is installed, create a new dashboard with a Panel (1 card) layout view, click Add Card, select Manual Card, switch to the YAML editor, and simply paste:

```yaml
type: custom:wine-cellar-card
```

Save and Wine Cellar Manager will be ready for you!

The card follows your Home Assistant theme, light or dark. To use the wood texture background instead, add `background: wood`:

```yaml
type: custom:wine-cellar-card
background: wood
```

### Obtaining a Gemini API Key

A Gemini API key can be used regardless of the model selected, but some may require payment information. Using `gemini-3.6-flash` is free but has daily limits. It's sufficient for Wine Cellar Manager and, as such, I did not implement any other AI option. Note that `gemini-3.6-flash`, like any AI, is far from perfect and can hallucinate or pull erroneous information. For most things it's reliable, but use with caution.

Getting a Gemini API key is completely free and takes just a few minutes using Google AI Studio.

- Go to the [Google AI Studio](https://aistudio.google.com/welcome) and log in using your Google account.
- Accept the Terms of Service if it is your first time using the platform.
- Click **Get API key** in the left-hand sidebar.
- Click **Create API key**.
- Select or create a Google Cloud Project when prompted, then click **Create key**.
- Copy your API key and store it securely.

## Main characteristics of Wine Cellar Manager

- Unlimited number of unique cellars which can be named individually. Each cellar can be given a color, picked from swatches, which is used for the frame of its cabinet so cellars are easy to tell apart.

![Create cellar](images/add_cellar.png)

- Each shelf of each cellar can be individually configured: front and/or back rows + number of bottles per row. This lets users adapt to cellars which can have moving racks at the top and fixed shelves at the bottom, for instance. Each shelf can be named individually.

![Rows with different characteristics](images/different_rows.png)

- Intelligent Storage Optimization: If you have multiple identical bottles (same name and producer), the integration automatically links them to a single shared label image on your server disk, preventing storage duplication when cloning or adding similar wines.

- Each bottle can be fully characterized with:
  - name (the only mandatory field)
  - label (JPG, PNG, WEBP, or GIF)
  - type from list (red, white, sparkling, rosé, orange, sweet or other)
  - varietals (the GUI handles complex varietal assemblies and replaces them with "Blend" as needed in Cellar view)
  - vintage
  - producer
  - region
  - country
  - beginning and ending of aging period
  - price
  - service temperature
  - alcohol concentration
  - personal rating
  - personal notes
  - clickable URL (default is SAQ.com, but any URL can be used)

- There are four views: Cellar, Compact, All Bottles, and Statistics.

## Cellar view

![Cellar View](images/main_view.png)

This is the default view. It shows a visual representation of all the cellars with useful information about each bottle. Each cellar is drawn as a cabinet whose frame takes the cellar's color. Its header shows how full it is (for example `28 / 40`), and the pencil button at the top right edits it.

Shelves appear in the order set within the cellar (can be modified), each with its name, the number of bottles it holds, and a rail underneath. On shelves with front and back rows, the back row sits above the front row, slightly smaller, on a shaded band and staggered into the gaps between the front bottles, representing how a physical shelf is actually configured. The rows are labelled Back and Front, and both are centered on the shelf.

The bottles are shown as cards. Each card is tinted and capped with the color of the wine type and displays the label image (or a drawn bottle when there is none), the name, the varietal (or region if the country is France), the vintage, and the rating.

A badge on the label shows the drinking window (for example `2025–27`), colored by aging status:
- **Blue**: too young
- **Green**: ready to drink
- **Orange**: peak (current year = last year of aging period)
- **Red**: past peak

No badge means the aging period is not set. A legend under the filters repeats these colors with the number of bottles in each state.

![Drag and drop](images/drag_drop.png)

Individual cards can be dragged and dropped at will. Bottles can be moved to an empty slot or swapped; the slot under the pointer is highlighted before you let go. This works seamlessly on PC, tablet, or mobile. This is, again, to duplicate how people physically interact with a cellar. It also works in the compact view.

![Bottle View](images/bottle.png)

Clicking on a card opens the Bottle View. This shows detailed information about this particular bottle. This is where the URL link appears. The physical location of the bottle (cellar, shelf, row, position) is also shown, along with a small map of the cellar that highlights the bottle's slot, and a timeline of its drinking window. There are buttons to Delete (all information removed from memory) or Consume (the bottle is removed from the cellar, but information remains for future use if a similar bottle is later added). There is an Edit button (see below) and a Copy button, which temporarily puts the bottle data in memory and closes the view. A banner then confirms the copy and every empty slot pulses; clicking one copies all the fields into this new slot, making it quick to add a second similar bottle. The copy can be cancelled from the banner, and expires after 10 minutes.

![Edit View](images/edit_bottle.png)

The Edit button brings up a new window. Each field can be filled at will, with only Name being required. Autocomplete (by looking at existing and consumed wines) is active for name, producer, varietal, region, and country. There is also an option to directly search the history by typing any of the main fields. The UI will recommend options which can be selected for auto-fill.

![Search previous entries](images/previous.png)

At the top of the Edit view is the option to upload the label image or a barcode image. The label image can be analyzed by Gemini to fill the bottle fields. If no label is present but a barcode image exists, clicking Analyze will ask Gemini to extract the barcode string. Once that string exists (either created by Gemini or entered manually by the user), clicking Analyze will have Gemini look at SAQ.com to determine the wine name and fill the bottle fields. The barcode image is then deleted to preserve storage. 

## Compact view

![Compact View](images/compact_view.png)

The compact view entirely duplicates the features of the Cellar view. The only difference is that each cellar is shown as if looking into an open wine fridge: every bottle is a glass bottle end colored by wine type, circled by its aging status color, with back-row bottles shown darker and nested between the front ones. Hovering a bottle shows its name, vintage and aging status. Apart from the bottle representation, this view is functionally identical to the Cellar view. It is particularly useful on mobile or to have a denser overview of several cellars. If the screen allows it, the card will put cellars side-by-side. This view is closer to what is typically seen in a cellar manager app.

## All bottles

![All Bottles](images/all_bottles.png)

This view is essentially a table view of all current bottles, grouped by type. The Location column shows where each bottle is stored (cellar, shelf, row and position), and sorting by it lists bottles in the order they sit in your cellars. The Aging column shows the drinking window with its status color. It allows sorting in ascending or descending order for any column. Clicking a line brings up the same Bottle view as with the Cellar and Compact views.

## Statistics

![Statistics](images/stats.png)

This view shows various information and statistics for the current inventory, including how many bottles reach their peak each upcoming year.

## Header controls

The header switches between the four views and offers two main buttons:

- **+ Cellar**: Opens a window allowing the creation and configuration of a new cellar. It is the same view as the one for editing a cellar.

![Cleanup tool](images/cleanup.png)

- **Clean-Up**: A very useful tool. It analyzes the whole inventory and identifies possible duplicates (for instance, with similar but not identical names, or misspelled varietals). For each case, it will propose a fix, letting the user decide which of the possible duplicates should be retained.

![Filters](images/filter.png)

In each view except Statistics, there are also filtering options. First, there is a search field. While any filter is active, matching bottles stay highlighted, the others fade, and the header shows how many bottles match. Under the filters, a summary line gives the number of bottles, the free slots, and how many bottles are in each aging state.

![Aging filter](images/aging.gif)

Second, there is a dropdown letting the user filter for bottles which are "Ready to drink" (highlights bottles whose "aging start" and "aging end" years cover the current year) or "Drink now" (bottles having reached their final peak year). 

![Filtering by type](images/type.png)

There are also two drop-down boxes letting the user filter by wine type or country.

## GUI details

- **Smart Column Balancing**: Every shelf spans the full width of its cabinet and its rows are centered, so shelves of different sizes line up in a clean, symmetrical layout.
- **Dynamic Mobile Adaptability**: Cellars scale to 100% of the screen width in portrait mode. A cabinet wider than the screen scrolls sideways as a whole, opens centered on its bottles, and keeps each shelf's name in view while scrolling.
- **Responsive Landscape Flow**: Automatically displays multiple cellars side-by-side on mobile landscape orientation, tablets, or wider PC monitors if screen real estate allows.
- **Theme Native**: All surfaces, text and accents come from the active Home Assistant theme, so the card follows Light and Dark mode and custom themes.
- **Optimized Real Estate**: Keeps the header section anchored on larger screens but hides structural padding on smaller phone viewports to preserve usability.
- **State Persistence**: Remembers your scroll position inside the dashboard even after minor interface refreshes.
- **Keyboard Friendly**: Bottles and empty slots can be reached with Tab and opened with Enter or Space.

## Notes
1. Large parts of the code were debugged, optimized, and refactored using advanced AI collaborative engines.
2. This integration was built primarily in French and translated during development. The card and the config flow follow your Home Assistant language and ship with English, French, German, Spanish, Italian, Dutch, Portuguese, and Polish. Other languages fall back to English. Some language quirks may remain.
3. This integration was built primarily for personal use. As such, some references relate to Québec (Canada), such as automatic CAD pricing conversions and native lookups on the state-owned alcohol monopoly "SAQ.com".

## Releases

Releases are automated. Every push to `main` runs the Release workflow, which:

1. bumps the version from the latest tag: patch by default, or minor/major when a commit message since the last release has a line containing only `#minor` or `#major`;
2. writes the new version to `manifest.json`, commits it, and tags it;
3. publishes a GitHub release with `wine_cellar_manager.zip` attached, which HACS installs.

Add `[skip release]` to a commit message to push without releasing, or run the workflow by hand from the Actions tab to pick the bump. Pull before pushing again, as each release adds a version commit to `main`. The release fails if `dist/wine-cellar-card.js` differs from the card in `custom_components/wine_cellar_manager/frontend/`, so copy it over after editing the card.

## Known bugs and To Do

- Barcode recognition does not work flawlessly for the moment due to image angle variations.
- Add a toggle in the Integration Options configuration to specify a custom domain or alternative source instead of SAQ.com for the default AI lookup analysis.