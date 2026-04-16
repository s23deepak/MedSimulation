# Changelog

All notable changes to MedSimulation and MedSimulation-App.

---

## [2026-04-16] - Mobile App Case Generation Fix + Patient Portrait Fix

### 🚀 Added

#### MedSimulation (Backend)
- **New API Endpoint**: `GET /api/cases/{case_id}` - Fetch full case data by ID
  - Returns complete case JSON including specialty, difficulty, source
  - Used by mobile app after case generation

#### MedSimulation-App (Mobile)
- **Auto-Open Simulation**: Cases now open automatically after generation
- **Visual "NEW" Indicator**: Green border + badge for newly generated cases (3s highlight)
- **Difficulty Color Coding**: Proper mapping from backend (`beginner/intermediate/advanced`) to app (`easy/medium/hard`)
- **Enhanced Logging**: Detailed console logs for debugging generation flow

### 🐛 Fixed

#### MedSimulation (Backend)
- **Route Collision**: Fixed `/api/cases/recommended` being caught by `/api/cases/{case_id}` route
  - Moved specific routes (`/recommended`, `/pending`, `/db`) BEFORE generic `{case_id}` route
  - This was causing 404 "Case not found" errors for the recommendation endpoint

#### MedSimulation-App (Mobile)
- **Cases Not Appearing**: Generated cases now properly appear at top of list
  - Fetch full case data by ID after generation
  - Prepend to list with proper mapping
- **FlatList Not Re-rendering**: React Native Web rendering issue fixed
  - Added `key` prop to FlatList for forced remount
  - Added `extraData` prop to trigger re-renders
- **CORS Blocking**: Changed to permissive `["*"]` for development

### 📝 Documentation

#### New Files
- `MedSimulation-App/UX_DECISIONS.md` - Design decisions and future considerations
  - Auto-open vs. confirmation dialog rationale
  - FlatList rendering fixes
  - Difficulty color coding
  - API architecture decisions
  - Future enhancement ideas (batch generation, smart auto-open, toast notifications)

#### Updated Files
- `MedSimulation/README.md`
  - Added Mobile App section with features and links
  - Updated with current architecture
- `MedSimulation-App/README.md`
  - Added "Recent Updates" section with fix summary
  - Updated features list with new capabilities
  - Added link to UX_DECISIONS.md

### 🔧 Technical Details

#### Backend Route Order (Critical Fix)
```python
# ✅ Correct order - specific routes first
@app.get("/api/cases/recommended")  # Specific
@app.get("/api/cases/pending")      # Specific
@app.get("/api/cases/db")           # Specific
@app.get("/api/cases/{case_id}")    # Generic - MUST be last
```

#### Mobile App Generation Flow
```typescript
// 1. Generate case
POST /api/cases/generate → { case_id, title, status }

// 2. Fetch full case data
GET /api/cases/{case_id} → { case_id, title, specialty, difficulty, source, ... }

// 3. Prepend to list
setCases(prev => [mappedCase, ...prev.filter(c => c.id !== case_id)])

// 4. Auto-open simulation
router.push(`/simulation?caseId=${case_id}`)
```

#### Difficulty Mapping
```typescript
const mapDifficulty = (difficulty: string): 'easy' | 'medium' | 'hard' => {
  switch (difficulty?.toLowerCase()) {
    case 'beginner': return 'easy';
    case 'intermediate': return 'medium';
    case 'advanced': return 'hard';
    default: return 'medium';
  }
};
```

### 🎨 UI/UX Changes

| Before | After |
|--------|-------|
| Alert dialog after generation | Auto-open simulation |
| No visual feedback | Green border + "NEW" badge (3s) |
| Cases may not appear | Guaranteed appearance at top |
| Difficulty badges may be wrong | Proper color coding |

### 🗑️ Removed: DALL-E Patient Portrait Generation

**Decision:** Removed patient portrait generation feature entirely.

**Reason:** DALL-E was generating images of doctors (male in white coat) instead of patients. The feature wasn't producing clinically useful images. The focus should be on medical imaging (X-rays, CT scans, ECGs) rather than photorealistic patient portraits.

**Files Changed:**
- `src/simulation/case_sources/ai_generator.py` - Removed portrait generation code, helper functions (`_extract_age_from_presentation`, `_extract_sex_from_presentation`), and OpenAI imports
- `src/simulation/media.py` - `generate_patient_portrait()` function now unused (can be removed in future cleanup)

**Impact:** All new cases will have empty `patient_image_url` field. Physical Exam tab will not display patient portraits.

### 📊 Files Changed

**Backend (`MedSimulation/`):**
- `main.py` - Route order fix, new `/api/cases/{case_id}` endpoint, changed generated case status from "pending" to "approved"
- `README.md` - Added mobile app section
- `CHANGELOG.md` - New file
- `src/simulation/case_sources/ai_generator.py` - Removed DALL-E portrait generation, simplified to set empty `patient_image_url`
- `templates/simulation.html` - Added `await loadCases()` after generation, cache busting version update

**Mobile (`MedSimulation-App/`):**
- `app/index.tsx` - Auto-open, case fetching, visual indicator, FlatList fixes
- `README.md` - Updated with new features and recent fixes
- `UX_DECISIONS.md` - New documentation file

---

## Previous Versions

### [PWA Support] - 2026-04-XX
- Added service worker for offline support
- Implemented install prompt
- Added offline page

### [Initial Release] - 2026-04-XX
- Clinical simulation engine with AI patient responses
- PubMed, Wiley, EndlessMedical case import
- Thompson Sampling bandit for recommendations
- PDF/JSON session export
