/**
 * Convert official Scryfall PNGs from URL import into project card documents
 * and prefer them over Drive matches.
 */

import { CardType as CardTypeSchema, OfficialCardImage, SourceType } from "@/common/schema_types";
import { computeSearchQueryHashKey, processQuery } from "@/common/processing";
import { CardDocuments, SearchQuery, SearchResults } from "@/common/types";

export const officialImageIdentifier = (image: OfficialCardImage): string =>
  `${image.scryfallId}:${image.face}`;

export function officialImageToCardDocument(image: OfficialCardImage) {
  return {
    identifier: officialImageIdentifier(image),
    cardType: CardTypeSchema.Card,
    name: image.name,
    priority: 1_000_000,
    source: "scryfall",
    sourceName: "Scryfall",
    sourceId: 0,
    sourceVerbose: "Scryfall (official PNG)",
    sourceType: SourceType.LocalFile,
    dpi: 150,
    searchq: processQuery(image.name),
    extension: "png",
    dateCreated: "",
    dateModified: "",
    size: 0,
    smallThumbnailUrl: image.pngUrl,
    mediumThumbnailUrl: image.pngUrl,
    language: "en",
    tags: ["official", "scryfall"],
    canonicalCard:
      image.expansionCode != null && image.collectorNumber != null
        ? {
            identifier: image.scryfallId,
            expansionName: image.expansionCode,
            expansionCode: image.expansionCode,
            collectorNumber: image.collectorNumber,
            smallThumbnailUrl: image.pngUrl,
            mediumThumbnailUrl: image.pngUrl,
          }
        : undefined,
  };
}

export function buildOfficialImageSearchResults(
  images: OfficialCardImage[]
): { documents: CardDocuments; searchResults: SearchResults } {
  const documents: CardDocuments = {};
  const searchResults: SearchResults = {};

  const addResult = (query: SearchQuery, identifier: string) => {
    const hashKey = computeSearchQueryHashKey(query);
    searchResults[hashKey] = [
      identifier,
      ...(searchResults[hashKey] ?? []).filter((id) => id !== identifier),
    ];
  };

  for (const image of images) {
    const identifier = officialImageIdentifier(image);
    documents[identifier] = officialImageToCardDocument(image);

    const printingQuery: SearchQuery = {
      query: processQuery(image.name),
      cardType: CardTypeSchema.Card,
      expansionCode: image.expansionCode,
      collectorNumber: image.collectorNumber,
    };
    addResult(printingQuery, identifier);

    // DFC backs from processLine omit expansion/collector; also index that query.
    if (image.expansionCode != null || image.collectorNumber != null) {
      addResult(
        {
          query: processQuery(image.name),
          cardType: CardTypeSchema.Card,
        },
        identifier
      );
    }
  }

  return { documents, searchResults };
}
