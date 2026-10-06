import type { Language } from "@/lib/i18n";

interface CurrencyOption {
  code: string;
  nameRu: string;
  nameEn: string;
}

// A curated set of ISO 4217 currencies, not the full ~180-code list — covers
// the majors plus the CIS/EU currencies most relevant to this app's
// Russian-speaking users. Intl.NumberFormat renders the correct symbol for
// any of these on its own; this list only supplies the human-readable label
// for the picker.
export const CURRENCIES: CurrencyOption[] = [
  { code: "AED", nameRu: "Дирхам ОАЭ", nameEn: "UAE Dirham" },
  { code: "AMD", nameRu: "Армянский драм", nameEn: "Armenian Dram" },
  { code: "AUD", nameRu: "Австралийский доллар", nameEn: "Australian Dollar" },
  { code: "AZN", nameRu: "Азербайджанский манат", nameEn: "Azerbaijani Manat" },
  { code: "BRL", nameRu: "Бразильский реал", nameEn: "Brazilian Real" },
  { code: "BYN", nameRu: "Белорусский рубль", nameEn: "Belarusian Ruble" },
  { code: "CAD", nameRu: "Канадский доллар", nameEn: "Canadian Dollar" },
  { code: "CHF", nameRu: "Швейцарский франк", nameEn: "Swiss Franc" },
  { code: "CNY", nameRu: "Китайский юань", nameEn: "Chinese Yuan" },
  { code: "CZK", nameRu: "Чешская крона", nameEn: "Czech Koruna" },
  { code: "EUR", nameRu: "Евро", nameEn: "Euro" },
  { code: "GBP", nameRu: "Фунт стерлингов", nameEn: "British Pound" },
  { code: "GEL", nameRu: "Грузинский лари", nameEn: "Georgian Lari" },
  { code: "HKD", nameRu: "Гонконгский доллар", nameEn: "Hong Kong Dollar" },
  { code: "HUF", nameRu: "Венгерский форинт", nameEn: "Hungarian Forint" },
  { code: "IDR", nameRu: "Индонезийская рупия", nameEn: "Indonesian Rupiah" },
  { code: "ILS", nameRu: "Израильский шекель", nameEn: "Israeli Shekel" },
  { code: "INR", nameRu: "Индийская рупия", nameEn: "Indian Rupee" },
  { code: "JPY", nameRu: "Японская иена", nameEn: "Japanese Yen" },
  { code: "KGS", nameRu: "Киргизский сом", nameEn: "Kyrgyzstani Som" },
  { code: "KRW", nameRu: "Южнокорейская вона", nameEn: "South Korean Won" },
  { code: "KZT", nameRu: "Казахстанский тенге", nameEn: "Kazakhstani Tenge" },
  { code: "MDL", nameRu: "Молдавский лей", nameEn: "Moldovan Leu" },
  { code: "MXN", nameRu: "Мексиканское песо", nameEn: "Mexican Peso" },
  { code: "NZD", nameRu: "Новозеландский доллар", nameEn: "New Zealand Dollar" },
  { code: "PLN", nameRu: "Польский злотый", nameEn: "Polish Zloty" },
  { code: "RON", nameRu: "Румынский лей", nameEn: "Romanian Leu" },
  { code: "RUB", nameRu: "Российский рубль", nameEn: "Russian Ruble" },
  { code: "SAR", nameRu: "Саудовский риял", nameEn: "Saudi Riyal" },
  { code: "SGD", nameRu: "Сингапурский доллар", nameEn: "Singapore Dollar" },
  { code: "THB", nameRu: "Тайский бат", nameEn: "Thai Baht" },
  { code: "TJS", nameRu: "Таджикский сомони", nameEn: "Tajikistani Somoni" },
  { code: "TMT", nameRu: "Туркменский манат", nameEn: "Turkmenistani Manat" },
  { code: "TRY", nameRu: "Турецкая лира", nameEn: "Turkish Lira" },
  { code: "UAH", nameRu: "Украинская гривна", nameEn: "Ukrainian Hryvnia" },
  { code: "USD", nameRu: "Доллар США", nameEn: "US Dollar" },
  { code: "UZS", nameRu: "Узбекский сум", nameEn: "Uzbekistani Som" },
  { code: "VND", nameRu: "Вьетнамский донг", nameEn: "Vietnamese Dong" },
  { code: "ZAR", nameRu: "Южноафриканский рэнд", nameEn: "South African Rand" },
];

export function getCurrencyLabel(code: string, language: Language): string {
  const currency = CURRENCIES.find((option) => option.code === code);
  const name = currency ? (language === "ru" ? currency.nameRu : currency.nameEn) : null;
  return name ? `${code} — ${name}` : code;
}
