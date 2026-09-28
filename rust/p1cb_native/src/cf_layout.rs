//! Organize flat v8unpack CF/CFE dumps into Class/Object layout with BSL prefixes.
//! Port of `parse_1c_build.cf_layout.organize_configuration_dir`.

use crate::bsl::{
    self, decode_utf8_sig, is_managed_form_file, split_file, write_bsl_renames_file, BIN_DIRNAME,
    BSL_PLACEHOLDER, BSL_PREFIX_COMMAND, BSL_PREFIX_OBJECT, LINE_ENDING, META_DIRNAME,
    OBJECTS_DIRNAME, RENAMES_ARROW,
};
use crate::metadata::{CONFIG_MODULE_SLOTS, CONFIGURATION_TYPE_UUID, METADATA_TYPES};
use once_cell::sync::Lazy;
use rayon::prelude::*;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Instant;

static RE_UUID: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    )
    .unwrap()
});

static RE_COLLECTION: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\{([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}),(\d+)((?:,[0-9a-fA-F-]{36})*)\}",
    )
    .unwrap()
});

static RE_OBJECT_NAME: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\{[01],0,([0-9a-fA-F-]{36})\},"([^"]+)""#).unwrap());

static RE_CONFIG_IDENTITY: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\{[01],0,([0-9a-fA-F-]{36})\},"([^"]+)""#).unwrap());

// Корень расширения (.cfe) в configinfo: `{2,<uuid>,`
static RE_CONFIGINFO_ROOT: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\{2,([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}),")
        .unwrap()
});

pub const CF_OBJECTS_FILENAME: &str = "cfobjects.txt";

const UTF8_BOM: &[u8] = &[0xEF, 0xBB, 0xBF];

#[derive(Debug, Clone)]
struct MetaObject {
    #[allow(dead_code)]
    type_uuid: String,
    object_uuid: String,
    name: String,
    class_folder: String,
    root_prefix: Option<String>,
    related_stems: HashSet<String>,
}

impl MetaObject {
    fn rel_dir(&self) -> String {
        if self.root_prefix.is_some() {
            String::new()
        } else {
            format!("{}/{}/{}", OBJECTS_DIRNAME, self.class_folder, self.name)
        }
    }
}

/// Top-level dump_dir index: stem -> entry names (O(1) lookup, no glob).
pub struct DumpIndex {
    dump_dir: PathBuf,
    by_stem: HashMap<String, Vec<String>>,
    names: HashSet<String>,
}

impl DumpIndex {
    pub fn build(dump_dir: &Path) -> io::Result<Self> {
        let mut by_stem: HashMap<String, Vec<String>> = HashMap::new();
        let mut names: HashSet<String> = HashSet::new();
        for entry in fs::read_dir(dump_dir)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            names.insert(name.clone());
            let stem = name
                .split_once('.')
                .map(|(s, _)| s)
                .unwrap_or(&name)
                .to_lowercase();
            by_stem.entry(stem).or_default().push(name);
        }
        for stem_names in by_stem.values_mut() {
            stem_names.sort();
        }
        Ok(Self {
            dump_dir: dump_dir.to_path_buf(),
            by_stem,
            names,
        })
    }

    fn stem_exists(&self, stem: &str) -> bool {
        self.by_stem
            .get(&stem.to_lowercase())
            .map(|v| !v.is_empty())
            .unwrap_or(false)
    }

    fn iter_stem_paths(&self, stem: &str) -> Vec<PathBuf> {
        let mut result = Vec::new();
        if let Some(names) = self.by_stem.get(&stem.to_lowercase()) {
            for name in names {
                let path = self.dump_dir.join(name);
                if path.exists() {
                    result.push(path);
                }
            }
        }
        result
    }

    fn forget(&mut self, name: &str) {
        self.names.remove(name);
        let stem = name
            .split_once('.')
            .map(|(s, _)| s)
            .unwrap_or(name)
            .to_lowercase();
        if let Some(entries) = self.by_stem.get_mut(&stem) {
            entries.retain(|n| n != name);
            if entries.is_empty() {
                self.by_stem.remove(&stem);
            }
        }
    }

    fn take_stem_paths(&mut self, stem: &str) -> Vec<PathBuf> {
        let paths = self.iter_stem_paths(stem);
        for path in &paths {
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                self.forget(name);
            }
        }
        paths
    }

    fn remaining_paths(&self) -> Vec<PathBuf> {
        let mut names: Vec<&String> = self.names.iter().collect();
        names.sort();
        let mut paths = Vec::new();
        for name in names {
            let path = self.dump_dir.join(name);
            if path.exists() {
                paths.push(path);
            }
        }
        paths
    }
}

fn read_text(path: &Path) -> io::Result<String> {
    let raw = fs::read(path)?;
    decode_utf8_sig(&raw).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

fn name_from_text(text: &str, object_uuid: &str) -> String {
    let uuid_l = object_uuid.to_lowercase();
    for caps in RE_OBJECT_NAME.captures_iter(text) {
        if caps.get(1).unwrap().as_str().to_lowercase() == uuid_l {
            return caps.get(2).unwrap().as_str().to_owned();
        }
    }
    if let Some(caps) = RE_OBJECT_NAME.captures(text) {
        return caps.get(2).unwrap().as_str().to_owned();
    }
    object_uuid.to_owned()
}

fn config_uuid_from_root(dump_dir: &Path) -> Result<String, String> {
    let root_path = dump_dir.join("root");
    if !root_path.is_file() {
        // Расширение (.cfe) хранит корень в configinfo, а не в root.
        let configinfo_path = dump_dir.join("configinfo");
        if !configinfo_path.is_file() {
            return Err(format!(
                "CF dump has neither root nor configinfo file: '{}'",
                dump_dir.display()
            ));
        }
        let text = read_text(&configinfo_path).map_err(|e| e.to_string())?;
        return RE_CONFIGINFO_ROOT
            .captures(&text)
            .map(|caps| caps.get(1).unwrap().as_str().to_lowercase())
            .ok_or_else(|| {
                format!(
                    "Cannot find extension root UUID in '{}'",
                    configinfo_path.display()
                )
            });
    }
    let text = read_text(&root_path).map_err(|e| e.to_string())?;
    RE_UUID
        .find(&text)
        .map(|m| m.as_str().to_lowercase())
        .ok_or_else(|| format!("Cannot find configuration UUID in '{}'", root_path.display()))
}

fn parse_collections(config_text: &str) -> Vec<(String, Vec<String>)> {
    let mut result = Vec::new();
    for caps in RE_COLLECTION.captures_iter(config_text) {
        let type_uuid = caps.get(1).unwrap().as_str().to_lowercase();
        let count: usize = caps.get(2).unwrap().as_str().parse().unwrap_or(0);
        if count == 0 {
            continue;
        }
        if type_uuid == CONFIGURATION_TYPE_UUID {
            continue;
        }
        let group3 = caps.get(3).map(|m| m.as_str()).unwrap_or("");
        let mut uuids: Vec<String> = RE_UUID
            .find_iter(group3)
            .map(|m| m.as_str().to_lowercase())
            .collect();
        uuids.truncate(count);
        result.push((type_uuid, uuids));
    }
    result
}

fn discover_objects(
    dump_dir: &Path,
    index: &DumpIndex,
) -> Result<(String, String, Vec<MetaObject>), String> {
    let config_uuid = config_uuid_from_root(dump_dir)?;
    let config_path = dump_dir.join(&config_uuid);
    if !config_path.is_file() {
        return Err(format!(
            "Configuration descriptor missing: '{}'",
            config_path.display()
        ));
    }
    let config_text = read_text(&config_path).map_err(|e| e.to_string())?;
    let mut objects: Vec<MetaObject> = Vec::new();
    let mut top_level: HashSet<String> = HashSet::new();
    top_level.insert(config_uuid.clone());
    let mut desc_texts: HashMap<String, String> = HashMap::new();

    for (type_uuid, uuids) in parse_collections(&config_text) {
        let (class_folder, root_prefix) = METADATA_TYPES
            .get(type_uuid.as_str())
            .map(|(f, p)| ((*f).to_owned(), p.map(|s| s.to_owned())))
            .unwrap_or_else(|| (format!("Type_{}", &type_uuid[..8]), None));

        for object_uuid in uuids {
            top_level.insert(object_uuid.clone());
            let desc = dump_dir.join(&object_uuid);
            let name = if desc.is_file() {
                let text = read_text(&desc).map_err(|e| e.to_string())?;
                let n = name_from_text(&text, &object_uuid);
                desc_texts.insert(object_uuid.clone(), text);
                n
            } else {
                object_uuid.clone()
            };
            objects.push(MetaObject {
                type_uuid: type_uuid.clone(),
                object_uuid,
                name,
                class_folder: class_folder.clone(),
                root_prefix: root_prefix.clone(),
                related_stems: HashSet::new(),
            });
        }
    }

    for obj in &mut objects {
        obj.related_stems.insert(obj.object_uuid.clone());
        if let Some(text) = desc_texts.get(&obj.object_uuid) {
            for m in RE_UUID.find_iter(text) {
                let ref_l = m.as_str().to_lowercase();
                if top_level.contains(&ref_l) && ref_l != obj.object_uuid {
                    continue;
                }
                if index.stem_exists(&ref_l) {
                    obj.related_stems.insert(ref_l);
                }
            }
        }
    }
    Ok((config_uuid, config_text, objects))
}

fn safe_move(src: &Path, dest: &Path, ensure_parent: bool) -> io::Result<()> {
    if ensure_parent {
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent)?;
        }
    }
    if dest.exists() {
        if dest.is_dir() {
            fs::remove_dir_all(dest)?;
        } else {
            fs::remove_file(dest)?;
        }
    }
    fs::rename(src, dest)
}

/// Extract plain-text module file to .bsl and replace with placeholder.
fn extract_plain_module(module_path: &Path, dest_bsl: &Path) -> io::Result<bool> {
    if !module_path.is_file() {
        return Ok(false);
    }
    let raw = fs::read(module_path)?;
    let body = if raw.starts_with(UTF8_BOM) {
        &raw[3..]
    } else {
        &raw[..]
    };
    let decoded = match decode_utf8_sig(body) {
        Ok(s) => s,
        Err(_) => {
            // Password-protected / encrypted payload — leave as-is.
            return Ok(false);
        }
    };
    if decoded.trim().is_empty() {
        fs::write(dest_bsl, b"")?;
        fs::write(module_path, b"\r\n")?;
        return Ok(true);
    }
    fs::write(dest_bsl, body)?;
    let mut placeholder = Vec::with_capacity(3 + BSL_PLACEHOLDER.len());
    placeholder.extend_from_slice(UTF8_BOM);
    placeholder.extend_from_slice(BSL_PLACEHOLDER.as_bytes());
    fs::write(module_path, placeholder)?;
    Ok(true)
}

fn extract_root_prefixed_object(
    index: &mut DumpIndex,
    obj: &MetaObject,
    root: &Path,
    renames: &mut Vec<(String, String)>,
) -> Result<(), String> {
    let root_prefix = obj.root_prefix.as_ref().unwrap();
    let bin_dir = root.join(BIN_DIRNAME);
    fs::create_dir_all(&bin_dir).map_err(|e| e.to_string())?;

    let mut stems: Vec<&String> = obj.related_stems.iter().collect();
    stems.sort();
    for stem in stems {
        for src in index.take_stem_paths(stem) {
            let rel = src
                .file_name()
                .unwrap()
                .to_string_lossy()
                .into_owned();
            let dest = bin_dir.join(&rel);
            safe_move(&src, &dest, false).map_err(|e| e.to_string())?;
            renames.push((rel.clone(), format!("{}/{}", BIN_DIRNAME, rel)));
        }
    }

    let text_path = bin_dir.join(format!("{}.0", obj.object_uuid)).join("text");
    let command_text_path = bin_dir.join(format!("{}.2", obj.object_uuid)).join("text");
    let form_path = bin_dir.join(format!("{}.0", obj.object_uuid));
    let bsl_name = format!("{}{}.bsl", root_prefix, obj.name);
    let bsl_path = root.join(&bsl_name);

    if text_path.is_file() {
        if extract_plain_module(&text_path, &bsl_path).map_err(|e| e.to_string())? {
            renames.push((
                bsl_name,
                format!("{}/{}.0/text", BIN_DIRNAME, obj.object_uuid),
            ));
        }
    } else if command_text_path.is_file() {
        if extract_plain_module(&command_text_path, &bsl_path).map_err(|e| e.to_string())? {
            renames.push((
                bsl_name,
                format!("{}/{}.2/text", BIN_DIRNAME, obj.object_uuid),
            ));
        }
    } else if form_path.is_file() && !form_path.is_dir() {
        if split_file(&form_path, Some(&bsl_path)).map_err(|e| e.to_string())? {
            renames.push((
                bsl_name,
                format!("{}/{}.0", BIN_DIRNAME, obj.object_uuid),
            ));
        }
    }
    Ok(())
}

fn path_component_count(path: &Path) -> usize {
    path.components().count()
}

fn relative_posix(path: &Path, base: &Path) -> String {
    path.strip_prefix(base)
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_else(|_| path.to_string_lossy().replace('\\', "/"))
}

fn walk_files(dir: &Path, out: &mut Vec<PathBuf>) -> io::Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            walk_files(&path, out)?;
        } else {
            out.push(path);
        }
    }
    Ok(())
}

/// Extract modules inside an object mini-layout (bin already filled).
fn extract_object_modules(object_dir: &Path, object_uuid: &str) -> Result<(), String> {
    let bin_dir = object_dir.join(BIN_DIRNAME);
    if !bin_dir.is_dir() {
        return Ok(());
    }
    let meta_dir = object_dir.join(META_DIRNAME);
    fs::create_dir_all(&meta_dir).map_err(|e| e.to_string())?;

    let mut bsl_renames: Vec<(String, String)> = Vec::new();
    let mut renames_txt: Vec<String> = Vec::new();
    let mut handled_texts: HashSet<PathBuf> = HashSet::new();
    let mut texts: Vec<PathBuf> = Vec::new();
    let mut form_items: Vec<PathBuf> = Vec::new();
    let mut nested_names: HashMap<String, String> = HashMap::new();

    let object_descriptor = bin_dir.join(object_uuid);
    if object_descriptor.is_file() {
        let descriptor_text = read_text(&object_descriptor).map_err(|e| e.to_string())?;
        for caps in RE_OBJECT_NAME.captures_iter(&descriptor_text) {
            nested_names.insert(
                caps.get(1).unwrap().as_str().to_lowercase(),
                caps.get(2).unwrap().as_str().to_owned(),
            );
        }
    }

    let mut all_files = Vec::new();
    walk_files(&bin_dir, &mut all_files).map_err(|e| e.to_string())?;
    for path in &all_files {
        let rel = relative_posix(path, &bin_dir);
        renames_txt.push(format!(
            "{}{}{}/{}{}",
            rel, RENAMES_ARROW, BIN_DIRNAME, rel, LINE_ENDING
        ));
        let filename = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let parent_name = path
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .unwrap_or("");
        if filename == "text" {
            texts.push(path.clone());
        } else if is_managed_form_file(path)
            && filename
                .strip_suffix(".0")
                .map(|stem| !stem.eq_ignore_ascii_case(object_uuid))
                .unwrap_or(false)
        {
            form_items.push(path.clone());
        } else if filename == "module" && parent_name.ends_with(".0") {
            form_items.push(path.clone());
        }
    }

    let add_plain = |text_path: &Path,
                     bsl_name: &str,
                     raw_opt: Option<&[u8]>,
                     handled: &mut HashSet<PathBuf>,
                     renames: &mut Vec<(String, String)>|
     -> Result<(), String> {
        if handled.contains(text_path) {
            return Ok(());
        }
        let owned: Vec<u8>;
        let raw: &[u8] = if let Some(r) = raw_opt {
            r
        } else {
            if !text_path.is_file() {
                return Ok(());
            }
            owned = fs::read(text_path).map_err(|e| e.to_string())?;
            &owned
        };
        let body = if raw.starts_with(UTF8_BOM) {
            &raw[3..]
        } else {
            raw
        };
        let decoded = decode_utf8_sig(body).map_err(|e| e.to_string())?;
        if decoded.trim().is_empty() {
            return Ok(());
        }
        let dest = object_dir.join(bsl_name);
        if dest.exists() {
            return Ok(());
        }
        fs::write(&dest, body).map_err(|e| e.to_string())?;
        let mut placeholder = Vec::with_capacity(3 + BSL_PLACEHOLDER.len());
        placeholder.extend_from_slice(UTF8_BOM);
        placeholder.extend_from_slice(BSL_PLACEHOLDER.as_bytes());
        fs::write(text_path, placeholder).map_err(|e| e.to_string())?;
        let rel = relative_posix(text_path, &bin_dir);
        renames.push((bsl_name.to_owned(), format!("{}/{}", BIN_DIRNAME, rel)));
        handled.insert(text_path.to_path_buf());
        Ok(())
    };

    let object_uuid_l = object_uuid.to_lowercase();
    add_plain(
        &bin_dir.join(format!("{}.0", object_uuid)).join("text"),
        &format!("{}Объект.bsl", BSL_PREFIX_OBJECT),
        None,
        &mut handled_texts,
        &mut bsl_renames,
    )?;

    for text_path in &texts {
        if handled_texts.contains(text_path) {
            continue;
        }
        let parent_name = text_path
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .unwrap_or("");
        if !parent_name.ends_with(".2") {
            continue;
        }
        let raw = fs::read(text_path).map_err(|e| e.to_string())?;
        let body = if raw.starts_with(UTF8_BOM) {
            &raw[3..]
        } else {
            &raw[..]
        };
        let text = decode_utf8_sig(body).map_err(|e| e.to_string())?;
        if text.trim().is_empty() {
            continue;
        }
        let stem = &parent_name[..parent_name.len() - 2];
        let is_command = text.contains("ОбработкаКоманды");
        if stem.to_lowercase() == object_uuid_l && !is_command {
            add_plain(
                text_path,
                &format!("{}Менеджер.bsl", BSL_PREFIX_OBJECT),
                Some(&raw),
                &mut handled_texts,
                &mut bsl_renames,
            )?;
            continue;
        }
        if !is_command {
            continue;
        }
        let mut cmd_name = nested_names
            .get(&stem.to_lowercase())
            .cloned()
            .unwrap_or_else(|| stem.to_owned());
        let desc = bin_dir.join(stem);
        if desc.is_file() {
            let desc_text = read_text(&desc).map_err(|e| e.to_string())?;
            if let Some(caps) = RE_OBJECT_NAME.captures(&desc_text) {
                cmd_name = caps.get(2).unwrap().as_str().to_owned();
            }
        } else if cmd_name == stem {
            cmd_name = stem
                .split('-')
                .next()
                .unwrap_or(stem)
                .to_owned();
        }
        let mut bsl_name = format!("{}{}.bsl", BSL_PREFIX_COMMAND, cmd_name);
        if object_dir.join(&bsl_name).exists() {
            let short = if stem.len() >= 8 { &stem[..8] } else { stem };
            bsl_name = format!("{}{}_{}.bsl", BSL_PREFIX_COMMAND, cmd_name, short);
        }
        add_plain(
            text_path,
            &bsl_name,
            Some(&raw),
            &mut handled_texts,
            &mut bsl_renames,
        )?;
    }

    form_items.sort_by(|a, b| {
        path_component_count(a)
            .cmp(&path_component_count(b))
            .then_with(|| a.to_string_lossy().cmp(&b.to_string_lossy()))
    });

    for item in &form_items {
        let companion = relative_posix(item, &bin_dir);
        let mut form_bsl_name: Option<String> = None;
        let filename = item.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if is_managed_form_file(item) {
            if let Some(form_name) = bsl::get_form_or_object_name(&bin_dir, filename) {
                form_bsl_name = Some(format!("{}{}.bsl", bsl::BSL_PREFIX_FORM, form_name));
            }
        } else if filename == "module" {
            let parent_name = item
                .parent()
                .and_then(|p| p.file_name())
                .and_then(|n| n.to_str())
                .unwrap_or("");
            if parent_name.ends_with(".0") {
                if let Some(form_name) = bsl::get_form_or_object_name(&bin_dir, parent_name) {
                    form_bsl_name = Some(format!("{}{}.bsl", bsl::BSL_PREFIX_FORM, form_name));
                }
            }
        }
        if let Some(mut form_bsl) = form_bsl_name {
            let mut dest = object_dir.join(&form_bsl);
            if dest.exists() {
                let internal_name = if is_managed_form_file(item) {
                    filename
                } else {
                    item.parent()
                        .and_then(|p| p.file_name())
                        .and_then(|n| n.to_str())
                        .unwrap_or("")
                };
                let internal_stem = internal_name.strip_suffix(".0").unwrap_or(internal_name);
                let short = &internal_stem[..internal_stem.len().min(8)];
                let base = form_bsl.strip_suffix(".bsl").unwrap_or(&form_bsl);
                form_bsl = format!("{}_{}.bsl", base, short);
                dest = object_dir.join(&form_bsl);
            }
            if split_file(item, Some(&dest)).map_err(|e| e.to_string())? {
                bsl_renames.push((form_bsl, format!("{}/{}", BIN_DIRNAME, companion)));
            }
        }
    }

    renames_txt.sort();
    let renames_path = meta_dir.join("renames.txt");
    // Python open("w", encoding="utf-8") — no BOM
    fs::write(&renames_path, renames_txt.join("").as_bytes()).map_err(|e| e.to_string())?;

    if !bsl_renames.is_empty() {
        let mut unique: HashSet<(String, String)> = HashSet::new();
        for e in &bsl_renames {
            unique.insert(e.clone());
        }
        let mut sorted: Vec<(String, String)> = unique.into_iter().collect();
        sorted.sort();
        write_bsl_renames_file(object_dir, &sorted).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn extract_config_modules(
    index: &mut DumpIndex,
    config_text: &str,
    root: &Path,
    renames: &mut Vec<(String, String)>,
) -> Result<(), String> {
    let Some(caps) = RE_CONFIG_IDENTITY.captures(config_text) else {
        return Ok(());
    };
    let identity = caps.get(1).unwrap().as_str().to_lowercase();
    let bin_dir = root.join(BIN_DIRNAME);

    // Collect slots to avoid holding Lazy borrow across mutations
    let slots: Vec<(u32, String)> = CONFIG_MODULE_SLOTS
        .iter()
        .map(|(k, v)| (*k, (*v).to_owned()))
        .collect();

    for (slot, role) in slots {
        let stem = format!("{}.{}", identity, slot);
        let src_dir = index.dump_dir.join(&stem);
        if !src_dir.exists() {
            continue;
        }
        let text_path = src_dir.join("text");
        if !text_path.is_file() {
            continue;
        }
        let dest_dir = bin_dir.join(&stem);
        if src_dir.exists() && !dest_dir.exists() {
            safe_move(&src_dir, &dest_dir, true).map_err(|e| e.to_string())?;
            index.forget(&stem);
            renames.push((stem.clone(), format!("{}/{}", BIN_DIRNAME, stem)));
        }
        let text_path = dest_dir.join("text");
        if !text_path.is_file() {
            continue;
        }
        let raw = fs::read(&text_path).map_err(|e| e.to_string())?;
        let decoded = decode_utf8_sig(&raw).map_err(|e| e.to_string())?;
        if decoded.trim().is_empty() {
            continue;
        }
        let bsl_name = format!("{}{}.bsl", BSL_PREFIX_OBJECT, role);
        if extract_plain_module(&text_path, &root.join(&bsl_name)).map_err(|e| e.to_string())? {
            renames.push((
                bsl_name,
                format!("{}/{}.{}/text", BIN_DIRNAME, identity, slot),
            ));
        }
    }
    Ok(())
}

/// Transform flat v8unpack CF dump into objects/Class/Name + root BSL layout.
/// Returns phase timings in seconds.
pub fn organize_configuration_dir(dump_dir: &str) -> Result<HashMap<String, f64>, String> {
    let dump_dir = PathBuf::from(dump_dir)
        .canonicalize()
        .map_err(|e| format!("Cannot resolve dump_dir '{}': {}", dump_dir, e))?;

    let mut timings: HashMap<String, f64> = HashMap::new();
    let mut phase = |name: &str, started: Instant| {
        let elapsed = started.elapsed().as_secs_f64();
        *timings.entry(name.to_owned()).or_insert(0.0) += elapsed;
    };

    let t0 = Instant::now();
    let mut index = DumpIndex::build(&dump_dir).map_err(|e| e.to_string())?;
    let (_config_uuid, config_text, objects) = discover_objects(&dump_dir, &index)?;
    phase("index_discover", t0);

    let rootbin = dump_dir.join(BIN_DIRNAME);
    let rootmeta = dump_dir.join(META_DIRNAME);
    let objects_root = dump_dir.join(OBJECTS_DIRNAME);
    fs::create_dir_all(&rootbin).map_err(|e| e.to_string())?;
    fs::create_dir_all(&rootmeta).map_err(|e| e.to_string())?;
    fs::create_dir_all(&objects_root).map_err(|e| e.to_string())?;
    index.forget(BIN_DIRNAME);
    index.forget(META_DIRNAME);
    index.forget(OBJECTS_DIRNAME);

    let mut root_renames: Vec<(String, String)> = Vec::new();
    let mut objects_index: Vec<(String, String)> = Vec::new();

    let t0 = Instant::now();
    for obj in &objects {
        if obj.root_prefix.is_none() {
            continue;
        }
        extract_root_prefixed_object(&mut index, obj, &dump_dir, &mut root_renames)?;
        objects_index.push((
            format!("@{}{}", obj.root_prefix.as_ref().unwrap(), obj.name),
            obj.object_uuid.clone(),
        ));
    }
    extract_config_modules(&mut index, &config_text, &dump_dir, &mut root_renames)?;
    phase("root_modules", t0);

    let mut extract_jobs: Vec<(PathBuf, String)> = Vec::new();
    let t0 = Instant::now();
    for obj in &objects {
        if obj.root_prefix.is_some() {
            continue;
        }
        let obj_dir = objects_root.join(&obj.class_folder).join(&obj.name);
        let objbin = obj_dir.join(BIN_DIRNAME);
        fs::create_dir_all(&objbin).map_err(|e| e.to_string())?;
        let mut stems: Vec<&String> = obj.related_stems.iter().collect();
        stems.sort();
        for stem in stems {
            for src in index.take_stem_paths(stem) {
                let dest = objbin.join(src.file_name().unwrap());
                safe_move(&src, &dest, false).map_err(|e| e.to_string())?;
            }
        }
        extract_jobs.push((obj_dir, obj.object_uuid.clone()));
        objects_index.push((obj.rel_dir(), obj.object_uuid.clone()));
    }
    phase("move_objects", t0);

    let t0 = Instant::now();
    if !extract_jobs.is_empty() {
        let results: Vec<Result<(), String>> = extract_jobs
            .par_iter()
            .map(|(obj_dir, object_uuid)| extract_object_modules(obj_dir, object_uuid))
            .collect();
        for r in results {
            r?;
        }
    }
    phase("extract_modules", t0);

    let t0 = Instant::now();
    for item in index.remaining_paths() {
        let name = item
            .file_name()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        if name == BIN_DIRNAME || name == META_DIRNAME || name == OBJECTS_DIRNAME {
            continue;
        }
        let dest = rootbin.join(&name);
        safe_move(&item, &dest, false).map_err(|e| e.to_string())?;
        index.forget(&name);
        root_renames.push((name.clone(), format!("{}/{}", BIN_DIRNAME, name)));
    }

    // cfobjects.txt
    {
        let mut sorted = objects_index.clone();
        sorted.sort_by(|a, b| a.0.cmp(&b.0));
        let mut out = String::new();
        for (rel, uuid) in &sorted {
            out.push_str(rel);
            out.push_str(RENAMES_ARROW);
            out.push_str(uuid);
            out.push_str(LINE_ENDING);
        }
        fs::write(rootmeta.join(CF_OBJECTS_FILENAME), out.as_bytes())
            .map_err(|e| e.to_string())?;
    }

    // root meta/renames.txt (skip .bsl targets)
    {
        let mut unique: HashSet<(String, String)> = HashSet::new();
        for e in &root_renames {
            unique.insert(e.clone());
        }
        let mut sorted: Vec<(String, String)> = unique.into_iter().collect();
        sorted.sort_by(|a, b| a.0.cmp(&b.0));
        let mut out = String::new();
        for (target, source) in &sorted {
            if target.ends_with(".bsl") {
                continue;
            }
            out.push_str(target);
            out.push_str(RENAMES_ARROW);
            out.push_str(source);
            out.push_str(LINE_ENDING);
        }
        fs::write(rootmeta.join("renames.txt"), out.as_bytes()).map_err(|e| e.to_string())?;
    }

    let bsl_root_entries: Vec<(String, String)> = {
        let mut unique: HashSet<(String, String)> = HashSet::new();
        for (t, s) in &root_renames {
            if t.ends_with(".bsl") {
                unique.insert((t.clone(), s.clone()));
            }
        }
        let mut v: Vec<_> = unique.into_iter().collect();
        v.sort();
        v
    };
    if !bsl_root_entries.is_empty() {
        write_bsl_renames_file(&dump_dir, &bsl_root_entries).map_err(|e| e.to_string())?;
    }
    phase("remaining_and_meta", t0);

    Ok(timings)
}
