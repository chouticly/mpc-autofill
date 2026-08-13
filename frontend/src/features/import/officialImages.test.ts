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

test("buildOfficialImageSearchResults prefers official ids first", () => {
  const { documents, searchResults } = buildOfficialImageSearchResults([
    {
      name: "Brainstorm",
      quantity: 1,
      scryfallId: "abcdef00-0000-0000-0000-000000000001",
      pngUrl:
        "https://cards.scryfall.io/png/front/a/b/abcdef00-0000-0000-0000-000000000001.png",
      face: Face.Front,
      expansionCode: "40K",
      collectorNumber: "192",
    },
  ]);
  expect(Object.keys(documents)).toHaveLength(1);
  const ids = Object.values(searchResults)[0];
  expect(ids[0]).toContain("abcdef00-0000-0000-0000-000000000001");
});
