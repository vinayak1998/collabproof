namespace CollabProof.S194R

/-!
The deliberately narrow trusted slice for the repository's FY 2024-25
section 194R interpretation. Money is exact natural-number paise. Cash-fee
(194J/194C) and GST decisions are intentionally absent.
-/

inductive EntityType where
  | individual
  | huf
  | firm
  | company
  deriving DecidableEq, Repr

inductive TaxBearer where
  | recipient
  | provider
  deriving DecidableEq, Repr

inductive Scope where
  | covered
  | unsupportedSpecVersion
  | unsupportedNonResident
  | unsupportedNoBusinessNexus
  deriving DecidableEq, Repr

structure SpecVersion where
  id : String
  deriving DecidableEq, Repr

def currentSpec : SpecVersion :=
  { id := "income-tax-1961-through-finance-no-2-act-2024+s194r-circulars-12-and-18-2022" }

structure Facts where
  isResident : Bool
  brandInBusiness : Bool
  brandEntity : EntityType
  precedingBusinessTurnoverPaise : Nat
  precedingProfessionReceiptsPaise : Nat
  panFurnished : Bool
  priorBenefitsPaise : Nat
  priorTdsPaise : Nat
  productFmvPaise : Nat
  productRetained : Bool
  taxBearer : TaxBearer
  deriving DecidableEq, Repr

structure Decision where
  scope : Scope
  benefitQualifies : Bool
  providerObligated : Bool
  aggregateBenefitPaise : Nat
  tdsDueNowPaise : Nat
  releaseGateRequired : Bool
  deriving DecidableEq, Repr

def thresholdPaise : Nat := 2000000
def businessCarveoutPaise : Nat := 1000000000
def professionCarveoutPaise : Nat := 500000000

def roundHalfUp (amount numerator denominator : Nat) : Nat :=
  let scaled := amount * numerator
  let q := scaled / denominator
  let r := scaled % denominator
  q + if 2 * r >= denominator then 1 else 0

def isSmallProvider (f : Facts) : Bool :=
  let eligibleEntity :=
    match f.brandEntity with
    | .individual | .huf => true
    | .firm | .company => false
  eligibleEntity &&
    f.precedingBusinessTurnoverPaise <= businessCarveoutPaise &&
    f.precedingProfessionReceiptsPaise <= professionCarveoutPaise

def unsupportedDecision (scope : Scope) : Decision :=
  { scope := scope
    benefitQualifies := false
    providerObligated := false
    aggregateBenefitPaise := 0
    tdsDueNowPaise := 0
    releaseGateRequired := false }

def decideCovered (f : Facts) : Decision :=
  let benefitQualifies := f.productFmvPaise > 0 && f.productRetained
  let providerObligated := !isSmallProvider f
  let rate := if f.panFurnished then 10 else 20
  let benefitValue := if benefitQualifies then f.productFmvPaise else 0
  let grossupTax :=
    if benefitValue > 0 && providerObligated && f.taxBearer == .provider then
      roundHalfUp benefitValue rate (100 - rate)
    else 0
  let aggregate := f.priorBenefitsPaise + benefitValue + grossupTax
  let totalTax :=
    if !providerObligated || aggregate <= thresholdPaise || benefitValue == 0 then 0
    else if f.taxBearer == .provider then
      roundHalfUp (f.priorBenefitsPaise + benefitValue) rate (100 - rate)
    else
      roundHalfUp aggregate rate 100
  let dueNow := totalTax - f.priorTdsPaise
  { scope := .covered
    benefitQualifies := benefitQualifies
    providerObligated := providerObligated
    aggregateBenefitPaise := aggregate
    tdsDueNowPaise := dueNow
    releaseGateRequired := dueNow > 0 && benefitValue > 0 }

def decide (spec : SpecVersion) (f : Facts) : Decision :=
  if spec != currentSpec then
    unsupportedDecision .unsupportedSpecVersion
  else if !f.isResident then
    unsupportedDecision .unsupportedNonResident
  else if !f.brandInBusiness then
    unsupportedDecision .unsupportedNoBusinessNexus
  else
    decideCovered f

end CollabProof.S194R
