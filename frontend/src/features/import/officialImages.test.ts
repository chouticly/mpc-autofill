import { Face } from "@/common/schema_types";
import {
  buildOfficialImageSearchResults,
  officialImageIdentifier,
  officialImageToCardDocument,
} from "@/features/import/officialImages";

test("officialImageToCardDocument uses Scryfall PNG URLs", () => {
  const image = {
    name: "Brainstorm",
    quantity: 4,
    scryfallId: "abcdef00-0000-0000-0000-000000000001",
    pngUrl:
      "https://cards.scryfall.io/png/front/a/b/abcdef00-0000-0000-0000-000000000001.png",
    face: Face.Front,
    expansionCode: "40K",
    collectorNumber: "192",
  };
  const document = officialImageToCardDocument(image);
  expect(document.identifier).toBe(officialImageIdentifier(image));
  expect(document.mediumThumbnailUrl).toBe(image.pngUrl);
  expect(document.extension).toBe("png");
  expect(document.canonicalCard?.expansionCode).toBe("40K");
});

test("buildOfficialImageSearchResults indexes DFC backs without printing filters", () => {
  const { searchResults } = buildOfficialImageSearchResults([
    {
      name: "Insectile Aberration",
      quantity: 1,
      scryfallId: "abff6c81-65a4-48fa-ba8f-580f87b0344a",
      pngUrl:
        "https://cards.scryfall.io/png/back/a/b/abff6c81-65a4-48fa-ba8f-580f87b0344a.png",
      face: Face.Back,
      expansionCode: "MID",
      collectorNumber: "47",
    },
  ]);
  // name-only query used for DFC backs
  const nameOnlyHash = Object.keys(searchResults).find((key) =>
    searchResults[key].some((id) => id.endsWith(":back"))
  );
  expect(Object.keys(searchResults).length).toBeGreaterThanOrEqual(2);
  expect(nameOnlyHash).toBeDefined();
});
