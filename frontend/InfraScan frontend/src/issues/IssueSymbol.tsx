import { issueTypeInfo } from "./constants.ts";
import { markerSymbolSvg } from "./markerIcon.ts";

type IssueSymbolProps = {
  issueType: string;
  fill?: string;
  textColor?: string;
};

/** Static marker shape for legends and filter labels. */
export function IssueSymbol({ issueType, fill = "#78909c", textColor = "#ffffff" }: IssueSymbolProps) {
  const type = issueTypeInfo(issueType);
  return (
    <span
      className="issue-symbol"
      aria-hidden="true"
      dangerouslySetInnerHTML={{
        __html: markerSymbolSvg({ shape: type.shape, fill, textColor, glyph: type.glyph }),
      }}
    />
  );
}
