"""Exact trusted fact envelope for the Lean Section 194R slice.

The broad :class:`collabproof.spec.Collab` model contains cash-TDS and GST
facts that Lean does not consume.  ``S194RFacts`` deliberately has no defaults:
every field represented in ``LeanProof/S194R.lean`` must be supplied before a
case can be confirmed or proved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .spec import Brand, Collab, Creator, EntityType, TaxBearer


FACT_PATHS = (
    "brand.entity_type",
    "brand.in_business",
    "brand.preceding_fy_business_turnover_paise",
    "brand.preceding_fy_profession_receipts_paise",
    "creator.is_resident",
    "creator.pan_furnished",
    "creator.fy_prior_benefits_from_brand_paise",
    "creator.fy_prior_194r_tds_paise",
    "transaction.product_fmv_paise",
    "transaction.product_retained",
    "transaction.tax_borne_by",
)
MAX_MONEY_PAISE = 10**20


@dataclass(frozen=True)
class S194RFacts:
    brand_entity_type: EntityType
    brand_in_business: bool
    brand_preceding_fy_business_turnover_paise: int
    brand_preceding_fy_profession_receipts_paise: int
    creator_is_resident: bool
    creator_pan_furnished: bool
    creator_fy_prior_benefits_from_brand_paise: int
    creator_fy_prior_194r_tds_paise: int
    product_fmv_paise: int
    product_retained: bool
    tax_borne_by: TaxBearer

    def __post_init__(self) -> None:
        booleans = {
            "brand.in_business": self.brand_in_business,
            "creator.is_resident": self.creator_is_resident,
            "creator.pan_furnished": self.creator_pan_furnished,
            "transaction.product_retained": self.product_retained,
        }
        for path, value in booleans.items():
            if type(value) is not bool:
                raise ValueError(f"{path} must be a boolean")

        money = {
            "brand.preceding_fy_business_turnover_paise": (
                self.brand_preceding_fy_business_turnover_paise
            ),
            "brand.preceding_fy_profession_receipts_paise": (
                self.brand_preceding_fy_profession_receipts_paise
            ),
            "creator.fy_prior_benefits_from_brand_paise": (
                self.creator_fy_prior_benefits_from_brand_paise
            ),
            "creator.fy_prior_194r_tds_paise": (
                self.creator_fy_prior_194r_tds_paise
            ),
            "transaction.product_fmv_paise": self.product_fmv_paise,
        }
        for path, value in money.items():
            if type(value) is not int or value < 0 or value > MAX_MONEY_PAISE:
                raise ValueError(
                    f"{path} must be an integer from 0 through "
                    f"{MAX_MONEY_PAISE} paise"
                )

        if not isinstance(self.brand_entity_type, EntityType):
            raise ValueError("brand.entity_type must be an EntityType")
        if not isinstance(self.tax_borne_by, TaxBearer):
            raise ValueError("transaction.tax_borne_by must be a TaxBearer")

    @classmethod
    def from_collab(cls, collab: Collab) -> "S194RFacts":
        """Project a broad collaboration onto the exact Lean-consumed fields."""
        return cls(
            brand_entity_type=collab.brand.entity_type,
            brand_in_business=collab.brand.in_business,
            brand_preceding_fy_business_turnover_paise=(
                collab.brand.preceding_fy_business_turnover_paise
            ),
            brand_preceding_fy_profession_receipts_paise=(
                collab.brand.preceding_fy_profession_receipts_paise
            ),
            creator_is_resident=collab.creator.is_resident,
            creator_pan_furnished=collab.creator.pan_furnished,
            creator_fy_prior_benefits_from_brand_paise=(
                collab.creator.fy_prior_benefits_from_brand_paise
            ),
            creator_fy_prior_194r_tds_paise=(
                collab.creator.fy_prior_194r_tds_paise
            ),
            product_fmv_paise=collab.product_fmv_paise,
            product_retained=collab.product_retained,
            tax_borne_by=collab.tax_borne_by,
        )

    def to_collab(self) -> Collab:
        """Build the broad model with explicit neutral values outside this slice."""
        return Collab(
            brand=Brand(
                entity_type=self.brand_entity_type,
                in_business=self.brand_in_business,
                preceding_fy_business_turnover_paise=(
                    self.brand_preceding_fy_business_turnover_paise
                ),
                preceding_fy_profession_receipts_paise=(
                    self.brand_preceding_fy_profession_receipts_paise
                ),
            ),
            creator=Creator(
                is_resident=self.creator_is_resident,
                pan_furnished=self.creator_pan_furnished,
                special_category_state=False,
                gst_registered=False,
                fy_prior_benefits_from_brand_paise=(
                    self.creator_fy_prior_benefits_from_brand_paise
                ),
                fy_prior_194r_tds_paise=self.creator_fy_prior_194r_tds_paise,
                fy_prior_cash_fees_from_brand_paise=0,
                fy_prior_cash_tds_paise=0,
                fy_prior_aggregate_turnover_paise=0,
            ),
            cash_fee_paise=0,
            product_fmv_paise=self.product_fmv_paise,
            product_retained=self.product_retained,
            deliverable_linked=False,
            tax_borne_by=self.tax_borne_by,
        )

    def as_dict(self) -> dict[str, Any]:
        """Canonical nested representation shared by intake and proof artifacts."""
        return {
            "brand": {
                "entity_type": self.brand_entity_type.value,
                "in_business": self.brand_in_business,
                "preceding_fy_business_turnover_paise": (
                    self.brand_preceding_fy_business_turnover_paise
                ),
                "preceding_fy_profession_receipts_paise": (
                    self.brand_preceding_fy_profession_receipts_paise
                ),
            },
            "creator": {
                "is_resident": self.creator_is_resident,
                "pan_furnished": self.creator_pan_furnished,
                "fy_prior_benefits_from_brand_paise": (
                    self.creator_fy_prior_benefits_from_brand_paise
                ),
                "fy_prior_194r_tds_paise": self.creator_fy_prior_194r_tds_paise,
            },
            "transaction": {
                "product_fmv_paise": self.product_fmv_paise,
                "product_retained": self.product_retained,
                "tax_borne_by": self.tax_borne_by.value,
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "S194RFacts":
        """Parse only the exact nested schema; unknown or absent fields fail."""
        if type(raw) is not dict or set(raw) != {"brand", "creator", "transaction"}:
            raise ValueError("Section 194R facts require brand, creator, and transaction")
        brand = raw["brand"]
        creator = raw["creator"]
        transaction = raw["transaction"]
        if type(brand) is not dict or set(brand) != {
            "entity_type",
            "in_business",
            "preceding_fy_business_turnover_paise",
            "preceding_fy_profession_receipts_paise",
        }:
            raise ValueError("invalid brand fields in Section 194R facts")
        if type(creator) is not dict or set(creator) != {
            "is_resident",
            "pan_furnished",
            "fy_prior_benefits_from_brand_paise",
            "fy_prior_194r_tds_paise",
        }:
            raise ValueError("invalid creator fields in Section 194R facts")
        if type(transaction) is not dict or set(transaction) != {
            "product_fmv_paise",
            "product_retained",
            "tax_borne_by",
        }:
            raise ValueError("invalid transaction fields in Section 194R facts")
        try:
            return cls(
                brand_entity_type=EntityType(brand["entity_type"]),
                brand_in_business=brand["in_business"],
                brand_preceding_fy_business_turnover_paise=(
                    brand["preceding_fy_business_turnover_paise"]
                ),
                brand_preceding_fy_profession_receipts_paise=(
                    brand["preceding_fy_profession_receipts_paise"]
                ),
                creator_is_resident=creator["is_resident"],
                creator_pan_furnished=creator["pan_furnished"],
                creator_fy_prior_benefits_from_brand_paise=(
                    creator["fy_prior_benefits_from_brand_paise"]
                ),
                creator_fy_prior_194r_tds_paise=(
                    creator["fy_prior_194r_tds_paise"]
                ),
                product_fmv_paise=transaction["product_fmv_paise"],
                product_retained=transaction["product_retained"],
                tax_borne_by=TaxBearer(transaction["tax_borne_by"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid Section 194R fact value: {exc}") from exc
